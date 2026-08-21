"""Spam detection: rules, memory, question, execution.

The core of these tests is not "detects spam": that is decided in the end by a model and a
human. What is checked is that the *mechanics* are right: that a known sender is left alone,
that a forged known sender is precisely NOT left alone, that a subscribed newsletter does not
pass as rubbish, and above all that every decision lands in the memory and influences the
next mail.
"""
import pytest
from app.models.assistant import AssistantContact, AssistantPolicy, SpamVerdict
from app.models.notification import Notification
from app.services import spam_learn, spam_review
from app.services.spam_rules import evaluate, features, mail_text
from app.services.vault_contacts import (
    addresses_from_note, known_domains, named_collision, sync_contacts,
)
from conftest import make_user
from sqlalchemy import select

pytestmark = pytest.mark.asyncio


def _mail(**over) -> dict:
    """An inconspicuous payload, as the imap watcher delivers it."""
    payload = {
        "account": "privat", "folder": "INBOX", "uid": 4711,
        "from": [{"name": "Shop", "addr": "info@shop.de"}],
        "to": [{"name": "", "addr": "ich@meine-domain.de"}],
        "subject": "Ihre Bestellung",
        "message_id": "<abc123@shop.de>",
        "date": "2026-08-17T10:00:00+02:00",
        "timestamp": "2026-08-17T10:00:05+02:00",
        "body_text": "Danke für Ihre Bestellung.",
        "links": [],
        "attachments": [],
        "headers": {
            "Authentication-Results": "mx.meine-domain.de; spf=pass; dkim=pass; dmarc=pass",
            "Return-Path": "<bounce@shop.de>",
            "Received-Count": 3,
        },
    }
    payload.update(over)
    return payload


# --- Regeln -------------------------------------------------------------------------

async def test_a_clean_mail_without_suspicion():
    res = evaluate(_mail(), my_addresses=frozenset({"ich@meine-domain.de"}))
    assert res.score == 0.0
    assert res.sender_email == "info@shop.de"
    assert res.signals == []


async def test_the_forgery_pattern_fires():
    """SPF failed, a foreign return path, a foreign reply address, a disguised name."""
    res = evaluate(_mail(
        **{"from": [{"name": "DHL Zustellung <service@dhl.de>", "addr": "x@dhl-tracking.xyz"}],
           "reply_to": [{"name": "", "addr": "kasse@4t7k.ru"}],
           "headers": {
               "Authentication-Results": "mx; spf=fail; dkim=fail; dmarc=fail",
               "Return-Path": "<bounce@4t7k.ru>",
               "Received-Count": 1,
           }}),
        my_addresses=frozenset({"ich@meine-domain.de"}))
    assert "spf_fail" in res.signals
    assert "dmarc_fail" in res.signals
    assert "returnpath_mismatch" in res.signals
    assert "replyto_fremd" in res.signals
    assert "absender_name_taeuscht" in res.signals
    assert "billig_tld" in res.signals
    assert res.score >= 0.9
    assert not res.is_newsletter


async def test_a_bounce_subdomain_is_not_a_mismatch():
    """`bounce.shop.de` to `shop.de` is usual and must raise no suspicion."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<b@bounce.shop.de>", "Received-Count": 3}))
    assert "returnpath_mismatch" not in res.signals


async def test_a_newsletter_stays_a_newsletter():
    """Clean bulk sending with an unsubscribe path is not spam; otherwise order confirmations
    disappear into the spam folder."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>",
        "List-Unsubscribe": "<https://shop.de/abmelden>",
        "Precedence": "bulk", "Received-Count": 3}))
    assert res.is_newsletter
    assert res.score == 0.0


async def test_bulk_mail_without_an_unsubscribe_path():
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass", "Precedence": "bulk", "Received-Count": 3}))
    assert "kein_unsubscribe_bei_bulk" in res.signals
    assert not res.is_newsletter


async def test_blind_copy_sending_stands_out():
    """One's own address stands nowhere, which is typical of bulk sending by blind copy."""
    res = evaluate(_mail(to=[{"name": "", "addr": "irgendwer@example.org"}]),
                   my_addresses=frozenset({"ich@meine-domain.de"}))
    assert "bcc_blast" in res.signals


async def test_own_domain_as_a_placeholder():
    """Whoever receives a whole domain cannot enumerate their aliases."""
    res = evaluate(_mail(to=[{"name": "", "addr": "shop-alias@meine-domain.de"}]),
                   my_addresses=frozenset({"*@meine-domain.de"}))
    assert "bcc_blast" not in res.signals


async def test_an_alias_is_kept_as_a_feature():
    """The addressed alias is a signal of its own: an alias only one provider knows and that
    suddenly receives foreign advertising has been sold."""
    res = evaluate(_mail(to=[{"name": "", "addr": "shop-alias@meine-domain.de"}]))
    feature_list = features(res, "Ihre Bestellung")
    assert "to:shop-alias@meine-domain.de" in feature_list
    assert "from:info@shop.de" in feature_list
    assert "dom:shop.de" in feature_list


async def test_a_freemail_domain_is_not_a_feature():
    """Everybody hangs off gmx.de, so the domain says nothing."""
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "wer@gmx.de"}]}))
    feature_list = features(res, "Hallo")
    assert "dom:gmx.de" not in feature_list
    assert "from:wer@gmx.de" in feature_list


# --- Echtheit: Ausrichtung ------------------------------------------------------------

async def test_a_dkim_pass_from_a_foreign_domain_stands_out():
    """A valid signature only says that SOMEBODY signed. Only the alignment with the sender
    domain makes a statement of it, and that is the most common way of forging with a "DKIM
    pass" anyway."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=none",
        "Return-Path": "<bounce@shop.de>",
        "DKIM-Domains": ["versender-xy.top"], "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" in res.signals
    assert not res.is_newsletter


async def test_a_passing_dmarc_ends_the_alignment_question():
    """DMARC IS the alignment check: if it passes, something is aligned by definition. Without
    this brake the rule fires on every mailing list contribution and every Google Workspace
    sender, which measured against real post is the most common false alarm of all."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>",
        "DKIM-Domains": ["shop-io.20251104.gappssmtp.com"], "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" not in res.signals


async def test_a_mailing_list_may_counter_sign():
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=none",
        "Return-Path": "<bounce@shop.de>", "List-Id": "<xiegu.groups.io>",
        "List-Unsubscribe": "<https://groups.io/ab>",
        "DKIM-Domains": ["groups.io"], "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" not in res.signals


async def test_a_dkim_subdomain_is_aligned():
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>",
        "DKIM-Domains": ["mail.shop.de"], "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" not in res.signals


async def test_the_dkim_domain_from_authentication_results():
    """Without a `DKIM-Signature` in the payload, `header.d=` carries the same information."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass header.d=fremd.top; dmarc=none",
        "Return-Path": "<bounce@shop.de>", "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" in res.signals


# --- Verdict of one's own mail server (learned from real mailboxes) --------------------

async def test_spam_level_stars_are_read():
    """`X-Spam-Level: **************`, one star per point. The server has long assessed it,
    and ignoring that and rebuilding it oneself would be the worse copy."""
    res = evaluate(_mail(headers={"X-Spam-Level": "**************", "Received-Count": 3}))
    assert "server_spam_hoch" in res.signals
    assert res.score >= 0.7


async def test_a_middling_server_score():
    res = evaluate(_mail(headers={"X-Spam-Status": "No, score=6.2 tests=[…]",
                                  "Received-Count": 3}))
    assert "server_spam_mittel" in res.signals


async def test_a_spam_marker_in_the_subject():
    res = evaluate(_mail(subject="***SPAM*** Erste-Hilfe-Set anfordern"))
    assert "betreff_spam_markiert" in res.signals


async def test_the_server_verdict_beats_the_newsletter_brake():
    """An unsubscribe button does not turn recognised rubbish into a subscribed newsletter."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>", "X-Spam-Level": "***********",
        "List-Unsubscribe": "<https://shop.de/ab>", "Received-Count": 3}))
    assert not res.is_newsletter


# --- Forwarding, self-forgery, click counters (learned from real mailboxes) -------------

async def test_an_srs_return_path_is_no_suspicion():
    """On forwarding, one's own server rewrites the return path onto itself. Then one's own
    domain ALWAYS stands there, and a signal that fires on every mail is none."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<SRS0=m6Wz=GK=shop.de=info@meine-domain.de>", "Received-Count": 3}),
        my_addresses=frozenset({"*@meine-domain.de"}))
    assert "returnpath_mismatch" not in res.signals


async def test_a_verp_bounce_address_does_not_fire():
    """Serious sending services bounce over addresses of their own (`bounces+…-kickstarter@…`).
    From a forwarded mail there is therefore nothing to get from the return path."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<SRS0=YnP1=GK=bounce.dienst.de=bounces+145129-0b4c-shop.de@meine-domain.de>",
        "Received-Count": 3}),
        my_addresses=frozenset({"*@meine-domain.de"}))
    assert "returnpath_mismatch" not in res.signals


async def test_mail_from_my_own_address():
    """"From you to you" without a passed check: the oldest trick."""
    res = evaluate(_mail(**{"from": [{"name": "Ich", "addr": "ich@meine-domain.de"}],
                            "headers": {"Received-Count": 1}}),
                   my_addresses=frozenset({"*@meine-domain.de"}))
    assert "absender_bin_ich" in res.signals


async def test_real_own_mail_stays_unsuspicious():
    res = evaluate(_mail(**{"from": [{"name": "Ich", "addr": "ich@meine-domain.de"}],
                            "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                                        "Return-Path": "<ich@meine-domain.de>",
                                        "Received-Count": 3}}),
                   my_addresses=frozenset({"*@meine-domain.de"}))
    assert "absender_bin_ich" not in res.signals


async def test_a_click_counter_is_no_deception():
    """Newsletters route EVERY link over a counting service; that is the normal case."""
    res = evaluate(_mail(links=[{"href": "https://ctrk.klclick.com/x", "text": "commodore.net"}]))
    assert "link_text_taeuscht" not in res.signals


async def test_a_link_on_the_sender_domain_is_no_deception():
    """`emails.kickstarter.com` with the sender `kickstarter.com`: the same yard."""
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "no-reply@kickstarter.com"}],
                            "links": [{"href": "https://emails.kickstarter.com/x",
                                       "text": "www.youtube.com"}]}))
    assert "link_text_taeuscht" not in res.signals


async def test_a_link_i_know_hides_nothing():
    res = evaluate(_mail(links=[{"href": "https://www.amazon.de/x", "text": "dpd.de"}]),
                   known_domains=frozenset({"amazon.de"}))
    assert "link_text_taeuscht" not in res.signals


async def test_an_unknown_redirect_in_a_list_is_only_one_point():
    res = evaluate(_mail(links=[{"href": "https://s8493.mjt99.example/x", "text": "eon.de"}],
                         headers={"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                                  "Return-Path": "<bounce@shop.de>",
                                  "List-Unsubscribe": "<https://eon.de/ab>",
                                  "Received-Count": 3}))
    assert "link_text_umgeleitet" in res.signals
    assert "link_text_taeuscht" not in res.signals


# --- Deception in the name --------------------------------------------------------------

async def test_a_punycode_sender():
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@xn--spakasse-9db.de"}]}))
    assert "punycode_absender" in res.signals


async def test_a_cyrillic_o_in_the_sender():
    """"sparkasse" with a Cyrillic "а" looks identical but is a different domain."""
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@spаrkasse.de"}]}))
    assert "schriftmischung" in res.signals


async def test_invisible_characters_in_the_subject():
    res = evaluate(_mail(subject="Ihre Rech​nung ist fällig"))
    assert "unsichtbare_zeichen" in res.signals


async def test_a_known_brand_as_a_foreign_subdomain():
    """`sparkasse.de.sicherheit.top` pulls a known brand into the visible address without
    owning it."""
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@sparkasse.de.sicherheit.top"}]}),
                   known_domains=frozenset({"sparkasse.de"}))
    assert "marke_als_subdomain" in res.signals


async def test_a_known_brand_with_a_hyphen():
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@sparkasse-de.top"}]}),
                   known_domains=frozenset({"sparkasse.de"}))
    assert "marke_als_subdomain" in res.signals


async def test_a_real_known_domain_is_no_abuse():
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@sparkasse.de"}]}),
                   known_domains=frozenset({"sparkasse.de"}))
    assert "marke_als_subdomain" not in res.signals


# --- Kopfzeilen-Hygiene ---------------------------------------------------------------

async def test_a_missing_message_id():
    res = evaluate(_mail(message_id=None))
    assert "msgid_fehlt" in res.signals


async def test_a_faked_reply():
    """"Re:" without a reference is an answer to a conversation that never existed."""
    res = evaluate(_mail(subject="Re: Ihre offene Rechnung", message_id="<a@shop.de>"))
    assert "fake_antwort" in res.signals


async def test_a_real_reply_is_fine():
    res = evaluate(_mail(subject="Re: Ihre Bestellung", message_id="<a@shop.de>", headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>", "In-Reply-To": "<vorher@shop.de>",
        "Received-Count": 3}))
    assert "fake_antwort" not in res.signals


async def test_a_shouted_and_stretched_subject():
    res = evaluate(_mail(subject="G.E.W.I.N.N.S.P.I.E.L!!!", message_id="<a@shop.de>"))
    assert "betreff_gestreckt" in res.signals
    assert "betreff_geschrien" in res.signals


async def test_a_random_sender_address():
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "a7f3c9e21b84d6f0@shop.de"}]}))
    assert "absender_zufaellig" in res.signals


async def test_a_date_in_the_future():
    res = evaluate(_mail(date="2026-08-20T10:00:00+02:00",
                         timestamp="2026-08-17T10:00:00+02:00"))
    assert "datum_versatz" in res.signals


async def test_manufactured_urgency():
    res = evaluate(_mail(headers={"X-Priority": "1", "Received-Count": 3}))
    assert "aufgesetzte_dringlichkeit" in res.signals


# --- Links ------------------------------------------------------------------------------

async def test_the_link_text_shows_a_different_domain_than_the_target():
    """The most reliable phishing indicator of all: in honest post it practically does not
    occur."""
    res = evaluate(_mail(links=[{"href": "https://4t7k.ru/login", "text": "www.paypal.de"}]))
    assert "link_text_taeuscht" in res.signals
    assert any("4t7k.ru" in g for g in res.reasons)


async def test_link_text_without_a_domain_claims_nothing():
    res = evaluate(_mail(links=[{"href": "https://shop.de/x", "text": "Hier klicken"}]))
    assert "link_text_taeuscht" not in res.signals


async def test_link_text_with_the_same_root_is_fine():
    res = evaluate(_mail(links=[{"href": "https://tracking.shop.de/x", "text": "shop.de"}]))
    assert "link_text_taeuscht" not in res.signals


async def test_a_link_to_a_bare_ip():
    res = evaluate(_mail(links=[{"href": "http://192.0.2.5/login", "text": "Konto prüfen"}]))
    assert "link_ip_adresse" in res.signals


async def test_the_at_trick_in_a_link():
    """Everything before the @ is decoration, the target is boese.top."""
    res = evaluate(_mail(links=[{"href": "https://paypal.de@boese.top/login", "text": "Konto"}]))
    assert "link_at_trick" in res.signals


async def test_a_shortener_and_a_cheap_tld():
    res = evaluate(_mail(links=[{"href": "https://bit.ly/x", "text": "mehr"},
                                {"href": "https://gewinn.top/y", "text": "hier"}]))
    assert "link_kuerzungsdienst" in res.signals
    assert "link_billig_tld" in res.signals


# --- Attachments ---------------------------------------------------------------------------

async def test_an_executable_attachment():
    res = evaluate(_mail(attachments=[{"filename": "Rechnung.exe", "content_type": "x", "size_bytes": 1}]))
    assert "anhang_ausfuehrbar" in res.signals


async def test_a_double_extension():
    res = evaluate(_mail(attachments=[{"filename": "Rechnung.pdf.exe"}]))
    assert "anhang_doppelendung" in res.signals
    assert "anhang_ausfuehrbar" in res.signals


async def test_an_svg_attachment_is_its_own_lighter_item():
    """SVG and HTML are sent as disguised login masks, but Google attaches its terms of use
    as .html as well. Hence an own, light entry instead of a common pot with executable
    files."""
    res = evaluate(_mail(attachments=[{"filename": "Dokument.svg"}]))
    assert "anhang_webseite" in res.signals
    assert "anhang_ausfuehrbar" not in res.signals
    assert res.score < 0.2


async def test_a_harmless_attachment_stays_harmless():
    res = evaluate(_mail(attachments=[{"filename": "Rechnung_2026.pdf"}]))
    assert "anhang_ausfuehrbar" not in res.signals


async def test_a_password_protected_archive():
    """An archive whose password stands in the text is blind to every scanner."""
    res = evaluate(_mail(attachments=[{"filename": "Unterlagen.zip"}]),
                   body="Das Passwort lautet 1234.")
    assert "anhang_archiv_mit_passwort" in res.signals


async def test_an_archive_without_a_password_in_the_text():
    res = evaluate(_mail(attachments=[{"filename": "Unterlagen.zip"}]), body="Anbei die Unterlagen.")
    assert "anhang_archiv_mit_passwort" not in res.signals


# --- Mail text from varying fields ---------------------------------------------------------

async def test_the_mail_text_finds_every_field():
    """The watcher delivers `body_text` OR `body_html_as_text`; whoever reads only `body`
    assesses an empty text for half of all mails."""
    assert mail_text({"body_text": "a"}) == "a"
    assert mail_text({"body_html_as_text": "b"}) == "b"
    assert mail_text({"body": "c"}) == "c"
    assert mail_text({}) == ""


# --- Vault-Kontakte ------------------------------------------------------------------

async def test_addresses_from_a_note_keep_their_origin_apart():
    note = (
        "---\n"
        "tags:\n  - kontakt\n"
        "email: rainer@t-online.de\n"
        "email_afu:\n"
        "  - dl1abc@verband.de\n"
        "telefon: '+49123'\n"
        "---\n\n"
        "# Rainer\n\nSchrieb mir von buero@firma.de aus.\n"
    )
    found = dict(addresses_from_note(note))
    assert found["rainer@t-online.de"] == "frontmatter"
    assert found["dl1abc@verband.de"] == "frontmatter"
    assert found["buero@firma.de"] == "body"


async def test_example_addresses_are_left_out():
    note = "---\nemail: max@example.com\n---\n\nText\n"
    assert addresses_from_note(note) == []


async def test_the_vault_reconcile_mirrors(db, tmp_path):
    folder = tmp_path / "03 Bereiche" / "Kontakte"
    folder.mkdir(parents=True)
    (folder / "Rainer.md").write_text("---\nemail: rainer@t-online.de\n---\n", encoding="utf-8")
    user = await make_user(db, "dennis")

    await sync_contacts(db, user.id, str(tmp_path))
    rows = (await db.execute(select(AssistantContact))).scalars().all()
    assert [r.email for r in rows] == ["rainer@t-online.de"]
    assert rows[0].domain == "t-online.de"

    # The note is gone, so the entry is gone (a mirror, not a stock of its own).
    (folder / "Rainer.md").write_text("---\nemail: neu@t-online.de\n---\n", encoding="utf-8")
    await sync_contacts(db, user.id, str(tmp_path))
    rows = (await db.execute(select(AssistantContact))).scalars().all()
    assert [r.email for r in rows] == ["neu@t-online.de"]


async def test_an_empty_vault_does_not_clear_anything(db, tmp_path):
    """A vault that is not mounted or half synchronised must not delete the acquittal list;
    otherwise half the family counts as foreign after a sync hiccup."""
    user = await make_user(db, "dennis")
    db.add(AssistantContact(owner_user_id=user.id, email="opa@t-online.de",
                            domain="t-online.de"))
    await db.commit()
    await sync_contacts(db, user.id, str(tmp_path))
    rows = (await db.execute(select(AssistantContact))).scalars().all()
    assert len(rows) == 1


# --- Chef-Masche (BEC) ------------------------------------------------------------------

async def _contact(db, owner_id, name: str, email: str) -> None:
    db.add(AssistantContact(owner_user_id=owner_id, email=email, name=name,
                            domain=email.split("@", 1)[1], source_kind="frontmatter"))
    await db.commit()


async def test_a_known_name_from_a_foreign_address(db):
    """No link, no attachment, no technical forgery: only a borrowed name. Only the contact
    stock gives that away."""
    user = await make_user(db, "dennis")
    await _contact(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    assert await named_collision(db, user.id, "Rainer Beispiel", "r.beispiel@gmx-mail.top") \
        == "Rainer Beispiel"


async def test_the_same_person_is_no_collision(db):
    user = await make_user(db, "dennis")
    await _contact(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    assert await named_collision(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de") == ""


async def test_salutation_and_reversed_spelling(db):
    user = await make_user(db, "dennis")
    await _contact(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    assert await named_collision(db, user.id, "Herr Dr. Rainer Beispiel", "x@fremd.top")
    assert await named_collision(db, user.id, "Beispiel, Rainer", "x@fremd.top")


async def test_a_one_word_name_does_not_fire(db):
    """"Info" or "support" are not people, and a single part name would match by chance
    constantly."""
    user = await make_user(db, "dennis")
    await _contact(db, user.id, "Support", "support@shop.de")
    assert await named_collision(db, user.id, "Support", "support@fremd.top") == ""


async def test_known_domains_without_prose(db):
    user = await make_user(db, "dennis")
    await _contact(db, user.id, "Rainer", "rainer@sparkasse.de")
    db.add(AssistantContact(owner_user_id=user.id, email="wer@zufall.top",
                            domain="zufall.top", source_kind="body"))
    await db.commit()
    domains = await known_domains(db, user.id)
    assert "sparkasse.de" in domains
    assert "zufall.top" not in domains


async def test_the_boss_scam_raises_suspicion(db):
    """End to end: a technically impeccable mail that pretends to be an acquaintance.

    The borrowed name alone deliberately carries no verdict (acquaintances write from their
    second address as well); only together with a redirected reply and manufactured urgency
    does it become a question.
    """
    user = await _owner(db)
    await _contact(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    verdict = await spam_review.judge(db, user.id, _mail(
        **{"from": [{"name": "Rainer Beispiel", "addr": "r.beispiel.buero@gmail.com"}],
           "reply_to": [{"name": "", "addr": "kasse@zahlung-xy.top"}],
           "subject": "Dringend: kurze Bitte",
           "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                       "X-Priority": "1", "Received-Count": 3}}),
        cls={"spam_score": 0.5, "category": "privat"})
    assert verdict["score"] >= verdict["frage_ab"]
    assert any("Rainer Beispiel" in g for g in verdict["reasons"])


async def test_a_borrowed_name_alone_carries_no_verdict(db):
    """An acquaintance writing from their second address must not pass as fraud: the vault
    never knows all the addresses of a person."""
    user = await _owner(db)
    await _contact(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    verdict = await spam_review.judge(db, user.id, _mail(
        **{"from": [{"name": "Rainer Beispiel", "addr": "rainer.beispiel@gmx.de"}],
           "subject": "Bilder von gestern"}),
        cls={"spam_score": 0.1, "category": "privat"})
    assert verdict["score"] < verdict["frage_ab"]


# --- Memory ---------------------------------------------------------------------------

async def _verdict(db, owner_id, feature_list, **over) -> SpamVerdict:
    fields = {"sender_email": "wer@spam.xyz", "subject": "Gewinn", **over}
    v = SpamVerdict(owner_user_id=owner_id, features=feature_list, **fields)
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def test_decisions_affect_the_next_mail(db):
    """The core: what the human decides has to change future assessments."""
    user = await make_user(db, "dennis")
    feature_list = ["from:wer@spam.xyz", "dom:spam.xyz", "wort:gewinn"]

    before, _, safe = await spam_learn.rate(db, user.id, feature_list)
    assert before == 0.5 and not safe      # no opinion without an observation

    for _ in range(3):
        v = await _verdict(db, user.id, feature_list)
        await spam_learn.remember(db, v, True)
        v.status = "spam"
    await db.commit()

    nachher, reasons, safe = await spam_learn.rate(db, user.id, feature_list)
    assert nachher > before
    assert safe, "a sender decided unanimously three times counts as resolved"
    assert any("3× spam" in g for g in reasons)


async def test_a_wanted_sender_is_learned(db):
    user = await make_user(db, "dennis")
    feature_list = ["from:news@verband.de", "dom:verband.de"]
    for _ in range(3):
        v = await _verdict(db, user.id, feature_list)
        await spam_learn.remember(db, v, False)
        v.status = "ham"
    await db.commit()

    score, _, safe = await spam_learn.rate(db, user.id, feature_list)
    assert score < 0.5 and safe


async def test_changing_ones_mind_undoes_the_old_count(db):
    """An error must not stay in the memory forever."""
    user = await make_user(db, "dennis")
    feature_list = ["from:news@verband.de"]
    v = await _verdict(db, user.id, feature_list)
    await spam_learn.remember(db, v, True)
    v.status = "spam"
    await db.commit()

    await spam_learn.remember(db, v, False, before="spam")
    await db.commit()
    from app.models.assistant import SpamFeatureStat
    row = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.feature == "from:news@verband.de"))).scalar_one()
    assert (row.spam_count, row.ham_count) == (0, 1)


async def test_examples_for_the_prompt(db):
    user = await make_user(db, "dennis")
    v = await _verdict(db, user.id, ["from:wer@spam.xyz"])
    await spam_review.decide(db, v, True, decided_by="telegram")
    lines = await spam_learn.examples(db, user.id)
    assert lines and "SPAM" in lines[0]


# --- Beurteilung im Ganzen -----------------------------------------------------------

async def _owner(db):
    user = await make_user(db, "dennis")
    user.telegram_chat_id = "4242"
    await db.commit()
    return user


async def test_a_known_contact_is_left_in_peace(db):
    user = await _owner(db)
    db.add(AssistantContact(owner_user_id=user.id, email="info@shop.de", domain="shop.de",
                            source_kind="frontmatter"))
    await db.commit()
    verdict = await spam_review.judge(db, user.id, _mail(),
                                          cls={"spam_score": 0.8, "category": "werbung"})
    assert verdict["bekannter_kontakt"] is True


async def test_a_forged_known_contact_stands_out(db):
    """The known name is the rewarding target; here the whitelist must NOT take hold."""
    user = await _owner(db)
    db.add(AssistantContact(owner_user_id=user.id, email="info@shop.de", domain="shop.de",
                            source_kind="frontmatter"))
    await db.commit()
    verdict = await spam_review.judge(db, user.id, _mail(headers={
        "Authentication-Results": "mx; spf=fail; dmarc=fail",
        "Return-Path": "<b@4t7k.ru>", "Received-Count": 1,
    }), cls={"spam_score": 0.5, "category": "sonstiges"})
    assert verdict["bekannter_kontakt"] is False
    assert verdict["score"] >= verdict["frage_ab"]
    assert any("a forgery is suspected" in g for g in verdict["reasons"])


async def test_high_suspicion_passes_the_immediate_threshold(db):
    """Whether that becomes an immediate card is decided by the flow; the height of the
    suspicion is decided here (see test_mail_intake_flow.py)."""
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, _mail(
        **{"from": [{"name": "", "addr": "x@4t7k.xyz"}],
           "reply_to": [{"name": "", "addr": "kasse@anders.ru"}],
           "headers": {"Authentication-Results": "mx; spf=fail; dkim=fail; dmarc=fail",
                       "Return-Path": "<b@anders.ru>", "Received-Count": 1}}),
        cls={"spam_score": 0.95, "spam_reason": "Paket-Vorwand", "category": "spam"})
    assert verdict["score"] >= verdict["sofort_ab"]


async def test_middling_suspicion_stays_below_the_immediate_threshold(db):
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, _mail(headers={
        "Authentication-Results": "mx; spf=softfail", "Received-Count": 1}),
        cls={"spam_score": 0.6, "category": "werbung"})
    assert verdict["frage_ab"] <= verdict["score"] < verdict["sofort_ab"]


async def test_the_unsuspicious_stays_below_the_asking_threshold(db):
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, _mail(),
                                          cls={"spam_score": 0.1, "category": "rechnung"})
    assert verdict["score"] < verdict["frage_ab"]


async def test_a_settled_sender_is_not_asked_about_again(db):
    """After three unanimous "wanted" the question should stop; that is the purpose of the
    learning."""
    user = await _owner(db)
    feature_list = features(evaluate(_mail(headers={"Authentication-Results": "mx; spf=softfail",
                                                "Received-Count": 1})), "Ihre Bestellung")
    for _ in range(3):
        v = await _verdict(db, user.id, feature_list, sender_email="info@shop.de")
        await spam_learn.remember(db, v, False)
        v.status = "ham"
    await db.commit()

    verdict = await spam_review.judge(db, user.id, _mail(headers={
        "Authentication-Results": "mx; spf=softfail", "Received-Count": 1}),
        cls={"spam_score": 0.7, "category": "werbung"})
    # The memory pulls the verdict below the question threshold, so the same mail no longer
    # triggers a second question. (It does not count as "settled": a forgery signal, here the
    # failed SPF, deliberately lifts the acquittal out of the memory.)
    assert verdict["learned_score"] < 0.5
    assert verdict["score"] < verdict["frage_ab"]


# --- Decision plus execution -------------------------------------------------------

@pytest.fixture
def imap_stub(monkeypatch):
    """Replace `imap-mcp` by a transcript."""
    calls = []

    async def fake_call_tool(url, tool, arguments, **kw):
        calls.append((tool, arguments))
        return {"content": [{"type": "text", "text": "verschoben nach Spam"}]}

    monkeypatch.setattr(spam_review, "call_tool", fake_call_tool)
    return calls


async def test_confirmation_moves_it_and_learns(db, imap_stub):
    user = await _owner(db)
    v = await _verdict(db, user.id, ["from:wer@spam.xyz", "wort:gewinn"],
                      account="privat", folder="INBOX", uid=4711)
    result = await spam_review.decide(db, v, True)

    assert imap_stub == [("mark_spam", {"account": "privat", "folder": "INBOX", "uid": 4711})]
    assert "verschoben" in result
    assert v.status == "spam" and v.decided_by == "telegram"
    score, _, _ = await spam_learn.rate(db, user.id, ["from:wer@spam.xyz"])
    assert score > 0.5


async def test_a_refusal_notes_the_sender(db, imap_stub):
    """"Not spam" is more than a no: the sender should not stand out at all in future."""
    user = await _owner(db)
    v = await _verdict(db, user.id, ["from:news@verband.de"], account="privat", folder="INBOX",
                      uid=99)
    v.sender_email, v.sender_domain = "news@verband.de", "verband.de"
    await db.commit()

    await spam_review.decide(db, v, False)
    assert imap_stub[0][0] == "mark_not_spam"
    rule = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.match_value == "news@verband.de"))).scalar_one()
    assert rule.match_kind == "sender"


async def test_a_failed_move_keeps_the_decision(db, monkeypatch):
    """IMAP briefly gone: the decision of the human was right regardless and stays in the
    memory; otherwise they would have to take it once more."""
    from app.services.mcp_client import McpError

    async def broken(*a, **k):
        raise McpError("Connection refused")

    monkeypatch.setattr(spam_review, "call_tool", broken)
    user = await _owner(db)
    v = await _verdict(db, user.id, ["from:wer@spam.xyz"], account="privat", folder="INBOX",
                      uid=1)
    result = await spam_review.decide(db, v, True)
    assert result.startswith("nicht verschoben")
    assert v.status == "spam"
    score, _, _ = await spam_learn.rate(db, user.id, ["from:wer@spam.xyz"])
    assert score > 0.5


async def test_the_digest_card_bundles_and_decides(db, imap_stub):
    import datetime as dt

    user = await _owner(db)
    old = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=5)
    for i in range(3):
        v = await _verdict(db, user.id, [f"from:wer{i}@spam.xyz"], account="privat",
                          folder="INBOX", uid=100 + i, score=0.6)
        v.created_at = old
    await db.commit()

    assert await spam_review.digest_due(db) == 1
    karte = (await db.execute(select(Notification).where(
        Notification.kind == "spam_digest"))).scalar_one()
    assert "3 suspected spam cases" in karte.title

    first = await db.get(SpamVerdict, karte.spam_verdict_id)
    count, error = await spam_review.decide_batch(db, first.digest_batch, True)
    assert (count, error) == (3, 0)
    assert len(imap_stub) == 3
    open_ones = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.status == "pending"))).scalars().all()
    assert open_ones == []


async def test_the_digest_card_skips_those_already_reported(db):
    """A case that already has a single card must not additionally turn up in the digest;
    otherwise the same thing is asked twice."""
    import datetime as dt

    user = await _owner(db)
    v = await _verdict(db, user.id, ["from:wer@spam.xyz"], score=0.95)
    v.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=5)
    db.add(Notification(user_id=user.id, spam_verdict_id=v.id, kind="spam_review",
                        chat_id="4242", title="🚩", body="…"))
    await db.commit()

    assert await spam_review.digest_due(db) == 0


# --- Disguised bulk sending (a case from 2026-08-18) ---------------------------------

def _phish(**over) -> dict:
    """The domain invoice mail: technically almost clean, elaborate HTML without a single
    link, the unsubscribe path only in the header, addressed to a collective mailbox."""
    payload = _mail(
        **{"from": [{"name": "Kundenkonto Verwaltung", "addr": "admin@unbekannt.example"}],
           "to": [{"name": "", "addr": "fragen@mitmachverein.de"}],
           "subject": "Ihre Domain-Rechnung wartet auf Bearbeitung",
           "body_text": "Handlungsbedarf: Domain mitmachverein.de - Zahlungsinformation",
           "links": [],
           "headers": {"Authentication-Results": "strato.com; dmarc=pass; dkim=pass; "
                                                 "spf=softfail",
                       "List-Unsubscribe": "<mailto:bounce@unbekannt.example>",
                       "Return-Path": "<admin@unbekannt.example>", "Received-Count": 6}})
    payload.update(over)
    return payload


async def test_disguised_bulk_mail_stands_out():
    res = evaluate(_phish(), my_addresses=frozenset({"ich@meine-domain.de"}))
    assert "abmeldung_nur_behauptet" in res.signals
    assert "geschaeft_an_rollenadresse" in res.signals
    # An unsubscribe path that does not exist in the body does not make a newsletter of it.
    assert res.is_newsletter is False
    assert res.score >= 0.9


async def test_a_facade_counts_only_when_the_technical_side_is_lacking():
    """Google Play, OpenAI and eQSL have no `<a href>` in the body and are genuine anyway:
    large senders unsubscribe over one-click in the header (RFC 8058). Whoever passes their
    checks may build their HTML as they like."""
    clean = _phish(**{"headers": {
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "List-Unsubscribe": "<mailto:abmelden@unbekannt.example>",
        "Return-Path": "<bounce@unbekannt.example>", "Received-Count": 3}})
    res = evaluate(clean, my_addresses=frozenset({"ich@meine-domain.de"}))
    assert "abmeldung_nur_behauptet" not in res.signals
    assert "html_ohne_links" not in res.signals


async def test_a_role_address_without_a_business_case_is_harmless():
    """Strangers write to `fragen@` constantly; that is the purpose of the address."""
    res = evaluate(_phish(subject="Frage zum nächsten Treffen",
                          body_text="Hallo, wann trefft ihr euch?"),
                   my_addresses=frozenset({"ich@meine-domain.de"}))
    assert "geschaeft_an_rollenadresse" not in res.signals


async def test_an_invoice_to_a_personal_address_is_harmless():
    res = evaluate(_phish(**{"to": [{"name": "", "addr": "ich@meine-domain.de"}]}),
                   my_addresses=frozenset({"ich@meine-domain.de"}))
    assert "geschaeft_an_rollenadresse" not in res.signals


async def test_accounting_is_not_a_role_address():
    """Some roles do have contracts, and the list separates that deliberately."""
    res = evaluate(_phish(**{"to": [{"name": "", "addr": "buchhaltung@verein.de"}]}),
                   my_addresses=frozenset({"ich@meine-domain.de"}))
    assert "geschaeft_an_rollenadresse" not in res.signals


async def test_a_nonbusiness_domain_beats_any_address():
    """With some domains there is no contractual business, and there `vorstand@` or
    `buchhaltung@` is no exception either, because it simply does not exist. That is a
    statement of the human (`spam_keine_geschaeftsdomains`), not a heuristic."""
    without = frozenset({"mitmachverein.de"})
    for address in ("fragen@mitmachverein.de", "vorstand@mitmachverein.de",
                    "buchhaltung@mitmachverein.de", "michael@mitmachverein.de"):
        res = evaluate(_phish(**{"to": [{"name": "", "addr": address}]}),
                       my_addresses=frozenset({"ich@meine-domain.de"}),
                       nonbusiness_domains=without)
        assert "geschaeft_an_domain_ohne_geschaeft" in res.signals, address

    # Without that statement it stays with the weaker role heuristic.
    res = evaluate(_phish(**{"to": [{"name": "", "addr": "buchhaltung@mitmachverein.de"}]}),
                   my_addresses=frozenset({"ich@meine-domain.de"}))
    assert "geschaeft_an_domain_ohne_geschaeft" not in res.signals
    assert "geschaeft_an_rollenadresse" not in res.signals


async def test_a_nonbusiness_domain_without_a_business_case_is_harmless():
    """The domain keeps receiving completely normal post, only no invoices."""
    res = evaluate(_phish(subject="Frage zum Treffen", body_text="Wann trefft ihr euch?",
                          **{"to": [{"name": "", "addr": "fragen@mitmachverein.de"}]}),
                   my_addresses=frozenset({"ich@meine-domain.de"}),
                   nonbusiness_domains=frozenset({"mitmachverein.de"}))
    assert "geschaeft_an_domain_ohne_geschaeft" not in res.signals


def _brands_mail(**over) -> dict:
    """The N26 phishing of 2026-08-19: technically flawless, forged only in the name.

    SPF and DKIM pass because the sender owns their own throwaway domain, an unsubscribe
    footer makes it look like a newsletter, and the mail programme shows nothing but
    "Support-N26". The whole lie sits in the display name.
    """
    payload = _mail(**{
        "account": "vorstand_b37", "uid": 336,
        "from": [{"name": "Support-N26", "addr": "support@fremde-firma.example"}],
        "to": [{"name": "", "addr": "vorstand@verein.example"}],
        "subject": "Ihre letzte Transaktion muss verifiziert werden – N26 Sicherheitsteam",
        "body_text": ("Ihre N26-Karte wurde gesperrt. Unser System hat bei der zuletzt "
                      "durchgeführten Zahlung eine Abweichung festgestellt."),
        "links": [{"href": "https://ecdylink.net/hqatxc", "text": "Karte jetzt reaktivieren"}],
        "headers": {
            "Authentication-Results": ("mxe90b; dkim=pass header.d=fremde-firma.example; "
                                       "spf=pass smtp.mailfrom=support@fremde-firma.example"),
            "List-Unsubscribe": "<mailto:unsubscribe@fremde-firma.example>",
            "Precedence": "bulk",
            "Return-Path": "<support@fremde-firma.example>",
            "Received-Count": 1,
            "X-Spam-Status": ("No, score=1.6 required=7.0 tests=DKIM_SIGNED,DKIM_VALID, "
                              "HTML_MESSAGE,SPF_PASS,URIBL_BLACK autolearn=no"),
        },
    })
    payload.update(over)
    return payload


async def test_a_brand_in_the_display_name_without_backing():
    res = evaluate(_brands_mail(), my_addresses=frozenset({"vorstand@verein.example"}))
    assert "marke_im_anzeigenamen" in res.signals
    # The unsubscribe footer must not save it: whoever forges the name is not a newsletter.
    assert res.is_newsletter is False


async def test_a_brand_with_backing_stays_quiet():
    """Contained is enough: `amazonses.com` sends for Amazon, `sparkasse-musterstadt.de` is one."""
    for name, addr in (("N26", "service@n26.com"),
                       ("Amazon.de", "versand@amazonses.com"),
                       ("Sparkasse Bamberg", "news@sparkasse-musterstadt.de")):
        res = evaluate(_brands_mail(**{"from": [{"name": name, "addr": addr}]}),
                       my_addresses=frozenset({"vorstand@verein.example"}))
        assert "marke_im_anzeigenamen" not in res.signals, name


async def test_a_free_name_is_not_a_brand():
    """A name that names no brand claims nothing: "Support" or "Dipl.-Ing." say nothing about
    who is writing, and an ambiguous abbreviation would fire on every second private mail."""
    for name in ("Support", "Dipl.-Ing. Klaus Meier", "Ups, da war noch was"):
        res = evaluate(_brands_mail(**{"from": [{"name": name, "addr": "a@b.example"}]}),
                       my_addresses=frozenset({"vorstand@verein.example"}))
        assert "marke_im_anzeigenamen" not in res.signals, name


async def test_a_blocklist_hit_counts_without_a_score():
    """The own mail server had the link on a blacklist and let the mail through anyway,
    because 1.6 of 7 points is not enough. The entry itself is the finding."""
    res = evaluate(_brands_mail(), my_addresses=frozenset({"vorstand@verein.example"}))
    assert "server_blockliste" in res.signals
    assert "server_spam_mittel" not in res.signals      # 1.6 points stay under the threshold


async def test_a_whitelist_is_not_a_blocklist_hit():
    """RCVD_IN_DNSWL_MED looks similar and means the opposite."""
    header = dict(_brands_mail()["headers"])
    header["X-Spam-Status"] = "No, score=-0.1 required=7.0 tests=DKIM_VALID,RCVD_IN_DNSWL_MED"
    res = evaluate(_brands_mail(headers=header), my_addresses=frozenset({"vorstand@verein.example"}))
    assert "server_blockliste" not in res.signals


async def test_phishing_passes_the_asking_threshold():
    """All three findings together: the mail has to become a question, not pass silently."""
    mail = _brands_mail()
    res = evaluate(mail, my_addresses=frozenset({"vorstand@verein.example"}),
                   nonbusiness_domains=frozenset({"verein.example"}), body=mail_text(mail))
    assert {"marke_im_anzeigenamen", "server_blockliste",
            "geschaeft_an_domain_ohne_geschaeft"} <= set(res.signals)
    assert res.score >= 0.9


# --- The identity the mail gives itself -------------------------------------------------

def _claim(name: str, addr: str, subject: str, text: str, links: list) -> dict:
    return _mail(**{"from": [{"name": name, "addr": addr}], "subject": subject,
                    "body_text": text,
                    "links": [{"href": h, "text": t} for h, t in links],
                    "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                                "Return-Path": f"<{addr}>", "Received-Count": 3}})


async def test_an_identity_without_backing_needs_no_brand_list():
    """The point of the rule: it works for a house nobody wrote down anywhere.

    The mail delivers the comparison value itself — it names the domain it claims to belong
    to. Whether that name stands on a list is irrelevant.
    """
    mail = _claim("Stadtwerke Hintertupfing", "service@swh-abrechnung.info",
                     "Ihre Abschlagszahlung konnte nicht gebucht werden",
                     "Bitte prüfen Sie Ihre Daten. Fragen? service@stadtwerke-hintertupfing.de",
                     [("https://kunden-portal-swh.top/login", "Jetzt prüfen")])
    res = evaluate(mail, my_addresses=frozenset({"ich@meine-domain.de"}),
                   body=mail_text(mail))
    assert "identitaet_ohne_deckung" in res.signals
    assert "marke_im_anzeigenamen" not in res.signals      # steht auf keiner Liste
    assert res.is_newsletter is False


async def test_real_mail_links_to_its_own_house():
    """The hardest honest case, taken from the real inbox: `Verti via CHECK24` sends over the
    comparison portal, names verti.de in the imprint — and links to click.email.verti.de.
    Exactly that link is the cover, and it is the reason the rule stays quiet."""
    mail = _claim("Verti via CHECK24", "agd7e36j9xadmv2.v@as.check24.de",
                     "Willkommen bei Verti, Herr Muster!",
                     "Herzlich willkommen! Es gelten die auf www.verti-empfehlen.de genannten "
                     "Bedingungen. Verti Versicherung AG, Rheinstraße 7A. www.verti.de",
                     [("https://click.email.verti.de/?qs=abc", "Zum Kundenportal")])
    res = evaluate(mail, my_addresses=frozenset({"ich@meine-domain.de"}),
                   body=mail_text(mail))
    assert "identitaet_ohne_deckung" not in res.signals


async def test_an_own_domain_is_no_foreign_claim():
    """Every mail quotes my address; that is not a claimed identity."""
    mail = _claim("Newsletter", "news@fremd.example", "Nachricht für meine-domain.de",
                     "Unsere Nachricht hat Sie über ich@meine-domain.de erreicht.",
                     [("https://fremd.example/x", "hier")])
    res = evaluate(mail, my_addresses=frozenset({"ich@meine-domain.de"}),
                   body=mail_text(mail))
    assert "identitaet_ohne_deckung" not in res.signals


async def test_a_mentioned_partner_is_no_claim():
    """Whoever mentions a service provider in passing does not present themselves as one."""
    mail = _claim("Der Verein", "info@verein.example", "Einladung zur Versammlung",
                     "Die Anmeldung läuft über eventbrite.com, bitte bis Freitag.",
                     [("https://verein.example/anmeldung", "Anmeldung")])
    res = evaluate(mail, my_addresses=frozenset({"ich@meine-domain.de"}),
                   body=mail_text(mail))
    assert "identitaet_ohne_deckung" not in res.signals


async def test_without_links_no_verdict_about_the_target():
    """Without a single link there is nothing to compare, and a mention alone says nothing."""
    mail = _claim("N26 Support", "support@fremd.example", "Ihr Konto",
                     "Melden Sie sich bei support@n26.com.", [])
    res = evaluate(mail, my_addresses=frozenset({"ich@meine-domain.de"}),
                   body=mail_text(mail))
    assert "identitaet_ohne_deckung" in res.signals or "marke_im_anzeigenamen" in res.signals


# --- Das Urteil des lokalen Modells ----------------------------------------------------

async def _n26(**over) -> dict:
    """The real case of 2026-08-19: a brand name in front of a foreign domain, and every
    authenticity check passing. The rules find exactly one signal for that."""
    return _mail(**{
        "from": [{"name": "N26", "addr": "kundensicherheitscenter@fremde-firma.example"}],
        "subject": "Neue Mitteilung",
        "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                    "Return-Path": "<b@fremde-firma.example>", "Received-Count": 3},
        **over})


async def test_a_model_fraud_verdict_prevails_over_weak_rules(db):
    """The mixture caps at 0.76 with a single rule signal, so no auto threshold was ever
    reachable, however sure the model was."""
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, await _n26(), cls={
        "spam_score": 0.95, "category": "sonstiges", "betrug": True,
        "spam_reason": "Phishing-Versuch mit gefälschtem Absender",
        "merkmale": [{"kennung": "marke_fremde_domain", "text": "gibt sich als N26 aus"}]})
    assert verdict["score"] >= 0.95
    assert verdict["modellurteil"] is True
    assert verdict["art"] == "phishing"
    assert any("attempted fraud" in g for g in verdict["reasons"])
    assert "llm:marke_fremde_domain" in verdict["features"]
    assert {"quelle": "modell", "kennung": "marke_fremde_domain",
            "text": "gibt sich als N26 aus"} in verdict["befunde"]


async def test_the_newsletter_brake_does_not_stop_the_fraud(db):
    """A phish that hangs an unsubscribe link under its footer is a "newsletter" to the
    rules. This test pins the ORDER: the floor has to come after the brake."""
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, await _n26(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "List-Unsubscribe": "<https://fremde-firma.example/ab>", "Received-Count": 3,
    }), cls={"spam_score": 0.95, "category": "werbung", "betrug": True})
    assert verdict["score"] >= 0.95


async def test_without_a_fraud_flag_the_blend_stays_as_before(db):
    """Without the flag nothing changes: the same mail keeps its mixed value."""
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, await _n26(), cls={
        "spam_score": 0.95, "category": "sonstiges", "betrug": False})
    assert verdict["modellurteil"] is False
    assert verdict["score"] < 0.95


async def test_the_wording_replaces_the_missing_field(db):
    """Models older than the field say it in the reason. Above the floor that counts."""
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, await _n26(), cls={
        "spam_score": 0.95, "category": "sonstiges",
        "spam_reason": "Phishing mit gefälschtem Absender"})
    assert verdict["modellurteil"] is True and verdict["score"] >= 0.95


async def test_negated_wording_fires_nothing(db):
    """"no fraud, just advertising" must not become the opposite verdict."""
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, _mail(), cls={
        "spam_score": 0.3, "category": "werbung", "spam_reason": "kein Betrug, nur Werbung"})
    assert verdict["modellurteil"] is False
    assert verdict["score"] < verdict["frage_ab"]


async def test_a_known_contact_is_no_shield_against_recognised_fraud(db):
    """The boss scam comes from a real, taken over address: technically nothing is wrong
    with it, and the graph checks the contact branch before the automatic one."""
    user = await _owner(db)
    db.add(AssistantContact(owner_user_id=user.id, email="info@shop.de", domain="shop.de",
                            source_kind="frontmatter"))
    await db.commit()
    verdict = await spam_review.judge(db, user.id, _mail(), cls={
        "spam_score": 0.95, "category": "sonstiges", "betrug": True})
    assert verdict["bekannter_kontakt"] is False
    assert verdict["score"] >= 0.95


async def test_a_learned_ham_sender_is_no_shield_against_fraud(db):
    """Three harmless mails do not vouch for the fourth: an account can be taken over."""
    user = await make_user(db, "dennis")
    for _ in range(3):
        v = await _verdict(db, user.id, ["from:info@shop.de"], sender_email="info@shop.de")
        await spam_learn.remember(db, v, False)
    await db.commit()
    verdict = await spam_review.judge(db, user.id, _mail(), cls={
        "spam_score": 0.95, "category": "sonstiges", "betrug": True})
    assert verdict["settled"] is False
    assert verdict["score"] >= 0.95


async def test_a_learned_spam_sender_stays_settled(db):
    """The counter test to the one above: the learned SPAM path clears away regardless of
    the auto threshold and must not be switched off along with it."""
    user = await make_user(db, "dennis")
    for _ in range(3):
        v = await _verdict(db, user.id, ["from:info@shop.de"], sender_email="info@shop.de")
        await spam_learn.remember(db, v, True)
    await db.commit()
    verdict = await spam_review.judge(db, user.id, _mail(), cls={
        "spam_score": 0.95, "category": "sonstiges", "betrug": True})
    assert verdict["settled"] is True and verdict["settled_verdict"] == "spam"


# --- The case of 2026-08-20: the PayPal receipt in the spam folder --------------------

async def _learned(db, owner_id, feature: str, spam: int, ham: int):
    from app.models.assistant import SpamFeatureStat
    db.add(SpamFeatureStat(owner_user_id=owner_id, feature=feature,
                           spam_count=spam, ham_count=ham))
    await db.commit()


async def test_ones_own_address_decides_nothing(db):
    """A receipt ended up in spam because the address it went to stood at spam four times.

    One's own recipient address is no feature of the mail but one of the mailbox: it appears in
    every wanted one just as in every spam. But because almost only spam is decided explicitly,
    it collects counters one-sidedly — and thereby became a self-fulfilling rule.
    """
    user = await _owner(db)
    await _learned(db, user.id, "to:paypal@meine.domain", spam=4, ham=0)

    score, reasons, safe = await spam_learn.rate(
        db, user.id, ["to:paypal@meine.domain"])
    assert safe is False, "die eigene Adresse darf nichts allein entscheiden"


async def test_agreement_means_without_contradiction(db):
    """Two strong features pointing in different directions are no agreement."""
    user = await _owner(db)
    await _learned(db, user.id, "from:service@paypal.de", spam=0, ham=282)
    await _learned(db, user.id, "dom:zahlung-xy.top", spam=9, ham=0)

    _score, _reasons, safe = await spam_learn.rate(
        db, user.id, ["from:service@paypal.de", "dom:zahlung-xy.top"])
    assert safe is False


async def test_a_known_sender_against_a_fraud_suspicion_gets_asked(db):
    """Model and memory contradict each other — then nobody decides alone.

    The real PayPal receipt was taken by the model for brand abuse and cleared away
    automatically, although this mailbox knew the sender a hundred times over as wanted. And
    because a moved mail gets a new number when recalled, that started over with every recall.
    """
    user = await _owner(db)
    await _learned(db, user.id, "from:service@paypal.de", spam=0, ham=282)

    verdict = await spam_review.judge(db, user.id, _mail(
        **{"from": [{"name": "PayPal", "addr": "service@paypal.de"}],
           "subject": "Beleg für Ihre Zahlung",
           "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                       "Received-Count": 3}}),
        cls={"spam_score": 0.95, "category": "phishing",
             "spam_reason": "Phishing-Versuch mit fremder Marke"})

    assert verdict["score"] < 1.0
    assert verdict["score"] >= verdict["frage_ab"], "gezeigt wird sie trotzdem"
    assert verdict["settled"] is False, "nichts ist geklärt, solange man sich streitet"
    assert any("wanted" in g for g in verdict["reasons"])


async def test_an_unknown_sender_stays_fraud(db):
    """The brake applies only to senders this mailbox really knows."""
    user = await _owner(db)
    verdict = await spam_review.judge(db, user.id, _mail(
        **{"from": [{"name": "PayPal", "addr": "service@paypal-sicherheit.top"}],
           "subject": "Ihr Konto wurde gesperrt"}),
        cls={"spam_score": 0.95, "category": "phishing",
             "spam_reason": "Phishing-Versuch mit fremder Marke"})
    assert verdict["score"] >= 0.95


async def test_whoever_contradicts_once_is_not_asked_again(db):
    """"I marked the mail as not spam twice" — that has to be enough.

    An explicit contradiction weighs more than any statistic and more than the model. Whoever
    contradicts twice and is asked again the third time is right to consider the detection
    broken.
    """
    user = await _owner(db)
    await _learned(db, user.id, "from:service@paypal.de", spam=0, ham=282)
    db.add(SpamVerdict(owner_user_id=user.id, sender_email="service@paypal.de",
                       subject="Beleg", status="ham", decided_by="mailbox", features=[]))
    await db.commit()

    verdict = await spam_review.judge(db, user.id, _mail(
        **{"from": [{"name": "PayPal", "addr": "service@paypal.de"}],
           "subject": "Beleg für Ihre Zahlung",
           "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                       "Received-Count": 3}}),
        cls={"spam_score": 0.95, "category": "phishing",
             "spam_reason": "Phishing-Versuch mit fremder Marke"})

    assert verdict["score"] < verdict["frage_ab"], "die Frage ist beantwortet"
    assert any("explicitly decided" in g for g in verdict["reasons"])


async def test_one_contradiction_is_no_free_pass(db):
    """A forged mail under the same name stays fraud — otherwise the sender released once
    would be the most convenient door into the house."""
    user = await _owner(db)
    await _learned(db, user.id, "from:service@paypal.de", spam=0, ham=282)
    db.add(SpamVerdict(owner_user_id=user.id, sender_email="service@paypal.de",
                       subject="Beleg", status="ham", decided_by="mailbox", features=[]))
    await db.commit()

    verdict = await spam_review.judge(db, user.id, _mail(
        **{"from": [{"name": "PayPal", "addr": "service@paypal.de"}],
           "reply_to": [{"name": "", "addr": "kasse@ganz-woanders.top"}],
           "subject": "Ihr Konto wurde gesperrt",
           "headers": {"Authentication-Results": "mx; spf=fail; dkim=fail; dmarc=fail",
                       "Return-Path": "<b@ganz-woanders.top>", "Received-Count": 1}}),
        cls={"spam_score": 0.95, "category": "phishing",
             "spam_reason": "Phishing-Versuch mit gefälschtem Absender"})
    assert verdict["score"] >= verdict["frage_ab"]
