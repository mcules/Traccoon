"""Two habits of a mailbox, and the folder as conversations.

Both are decisions somebody can get wrong in a way nobody notices: a trash that quietly
marks mail read on a SHARED mailbox destroys what the read marks are for there, and a
conversation view that groups by subject alone puts two different "Re: Rechnung" into one
row. So the rule is tested, not the wiring.
"""
import pytest

from app.services import mailbox


class Account:
    """Only what the rules ask about."""
    id = 4711
    folder_trash = "Trash"
    trash_marks_read = True


class Client:
    """A mailbox that writes down what was asked of it, in order."""

    def __init__(self, capabilities=(b"IMAP4REV1", b"THREAD=REFERENCES"), threads=()):
        self.log: list[tuple] = []
        self._capabilities = list(capabilities)
        self._threads = threads

    # -- what the rules use --
    def capabilities(self):
        return self._capabilities

    def has_capability(self, name):
        return name.encode() in self._capabilities

    def add_flags(self, uids, flags):
        self.log.append(("flags", list(uids), [f.decode() for f in flags]))

    def move(self, uids, target):
        self.log.append(("move", list(uids), target))

    def select_folder(self, name, readonly=False):
        self.log.append(("select", name))
        return {b"EXISTS": 9}

    def thread(self, algorithm, criteria):
        self.log.append(("thread", algorithm, list(criteria)))
        return self._threads

    def search(self, criteria):
        self.log.append(("search", list(criteria)))
        return [1, 2, 3]

    def fetch(self, uids, fields):
        self.log.append(("fetch", sorted(uids)))
        return {uid: {} for uid in uids}


def test_the_trash_marks_read_before_the_move():
    """After a MOVE the message has a new uid over there. Marked afterwards, the flag would
    have to be looked for in a folder nobody is reading."""
    client, account = Client(), Account()
    mailbox._mark_discarded(client, account, [7, 8], "Trash")
    assert client.log == [("flags", [7, 8], ["\\Seen"])]


def test_another_folder_is_not_the_trash():
    """Filing is not throwing away: a mail moved into an archive keeps its state."""
    client = Client()
    mailbox._mark_discarded(client, Account(), [7], "Archive")
    assert client.log == []


def test_a_mailbox_may_say_no():
    """On a shared mailbox the marks say what the OTHERS have already seen."""
    class Shared(Account):
        trash_marks_read = False

    client = Client()
    mailbox._mark_discarded(client, Shared(), [7], "Trash")
    assert client.log == []


def test_a_conversation_is_flattened_however_deep_it_is():
    """THREAD answers with a tree — an answer to an answer sits inside the answer."""
    assert mailbox._flatten((1, (2, (3,), (4,)), 5)) == [1, 2, 3, 4, 5]


def test_threads_come_back_newest_first(monkeypatch):
    """A thread answered today belongs at the top, even when it began in March."""
    client = Client(threads=((1, 2), (30,), (10, 11, 12)))
    monkeypatch.setattr(mailbox, "_imap", _lending(client))
    monkeypatch.setattr(mailbox, "_row", lambda uid, entry, folder="": {
        "uid": uid, "seen": uid % 2 == 0, "folder": folder})

    answer = mailbox._threaded_sync(Account(), "INBOX", "", 0, 10)

    assert answer["threaded"] is True
    assert answer["total"] == 3
    assert [m["uid"] for m in answer["messages"]] == [30, 12, 2]
    # A conversation carries its members, a single message does not carry a "1".
    assert "thread" not in answer["messages"][0]
    assert [k["uid"] for k in answer["messages"][1]["thread"]] == [12, 11, 10]
    assert answer["messages"][1]["thread_count"] == 3
    # 11 is odd and therefore unread in this test's `_row`.
    assert answer["messages"][1]["thread_unseen"] == 1


def test_a_server_without_threading_says_so(monkeypatch):
    """Rather no conversations than invented ones: grouping by subject alone puts two
    different "Re: Rechnung" into one row, and that is a mistake one does not see."""
    client = Client(capabilities=(b"IMAP4REV1",))
    monkeypatch.setattr(mailbox, "_imap", _lending(client))
    monkeypatch.setattr(mailbox, "_row", lambda uid, entry, folder="": {"uid": uid})

    answer = mailbox._threaded_sync(Account(), "INBOX", "", 0, 10)

    assert answer["threaded"] is False
    assert ("thread", "REFERENCES", ["ALL"]) not in client.log


def _lending(client):
    """`_imap` as a context manager over a prepared client."""
    import contextlib

    @contextlib.contextmanager
    def lend(_account):
        yield client
    return lend


# -- Conversations the server cannot see ------------------------------------

def _headers(message_id: str = "", answers: str = "") -> dict:
    """One fetched header block, as imapclient hands it over."""
    lines = []
    if message_id:
        lines.append(f"Message-ID: {message_id}")
    if answers:
        lines.append(f"In-Reply-To: {answers}")
    return {mailbox._THREAD_HEADERS_KEY: ("\r\n".join(lines) + "\r\n\r\n").encode()}


def test_an_encoded_reference_still_joins_the_conversation():
    """`In-Reply-To` must never carry RFC 2047 encoded words — senders do it anyway, and then
    the id inside is invisible to the server's own threading. Found on a real thread of four
    messages whose newest one stood alone in the list.
    """
    plain = "<AM0PR01MB6242B54AF@exchangelabs.com>"
    encoded = ("=?utf-8?q?=3CAM0PR01MB6242B54AF=40exchangelabs=2E?="
               "=?utf-8?q?com=3E?=")
    raw = {
        1: _headers("<root@example.org>"),
        2: _headers(plain, "<root@example.org>"),
        3: _headers("<answer@example.org>", encoded),
    }
    # The server sees two conversations: 3 answers nobody it can read.
    groups = mailbox._conversations(raw, [1, 2, 3], [[1, 2], [3]])

    assert len(groups) == 1, "die drei gehoeren zusammen"
    assert sorted(groups[0]) == [1, 2, 3]


def test_a_stranger_stays_its_own_conversation():
    """Only a named `Message-ID` joins two groups. A matching subject is the server's
    business, and two different "Re: Rechnung" are two conversations."""
    raw = {
        1: _headers("<a@example.org>"),
        2: _headers("<b@example.org>", "<never-heard-of@example.org>"),
    }
    groups = mailbox._conversations(raw, [1, 2], [[1], [2]])
    assert sorted(sorted(g) for g in groups) == [[1], [2]]


def test_the_server_grouping_is_never_taken_apart():
    """It does the hard part (RFC 5256, subject rule included). This only ever joins."""
    raw = {1: _headers("<a@example.org>"), 2: _headers("<b@example.org>")}
    groups = mailbox._conversations(raw, [1, 2], [[1, 2]])
    assert sorted(groups[0]) == [1, 2]
