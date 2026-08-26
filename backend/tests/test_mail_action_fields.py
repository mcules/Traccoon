"""A mail button may ask before it runs.

The occasion: filing a document is rarely the whole intention. "This is the policy for the
car, put the number into the contract note" is, and that sentence had nowhere to go — one
filed the file and told the assistant afterwards, somewhere else, without the document.

The fields are declared on the trigger, not built into one flow, so every button after this
one can ask the same way. What is pinned here is the part that would silently rot: only
declared fields reach the flow, and a required one is not a suggestion.
"""
import pytest
from app.api.mailbox import _fields

from conftest import make_user


def test_only_usable_fields_come_through():
    """Whatever stands in a graph comes from an editor and from hand-written JSON."""
    out = _fields({"fields": [
        {"name": "auftrag", "label": "Instruction", "type": "text", "required": True},
        {"label": "without a name"},          # cannot carry a value
        "not even an object",
        {"name": "menge", "type": "nonsense"},  # unknown type would render as nothing
    ]})
    assert [f["name"] for f in out] == ["auftrag", "menge"]
    assert out[0]["required"] is True
    assert out[1]["type"] == "text", "an unknown type falls back to something renderable"
    assert out[1]["label"] == "menge", "without a label the name has to do"


def test_a_trigger_without_fields_is_a_plain_button():
    assert _fields({"kind": "mail_action"}) == []


@pytest.fixture
async def flow(db):
    """The shipped attachment button, as a published flow."""
    from app.services import workflow_templates

    user = await make_user(db, "anna")
    d = await workflow_templates.create(db, "attachment-to-paperless", owner_id=user.id)
    await db.commit()
    return user, d


async def test_the_shipped_button_asks_for_the_instruction(db, flow):
    """And it asks optionally: leaving it empty has to stay one click."""
    from app.api.mailbox import _start_trigger
    from app.models.workflow import WorkflowVersion

    _user, d = flow
    version = await db.get(WorkflowVersion, d.current_version_id)
    fields = _fields(_start_trigger(version.graph))
    assert [f["name"] for f in fields] == ["auftrag"]
    assert fields[0]["required"] is False, "only filing it must stay one click"


async def test_the_flow_branches_on_the_field(db, flow):
    """Filled in, the assistant takes over; empty, it stays a plain upload."""
    from app.models.workflow import WorkflowVersion

    _user, d = flow
    version = await db.get(WorkflowVersion, d.current_version_id)
    nodes = {n["id"]: n for n in version.graph["nodes"]}
    branch = nodes["weiche"]["data"]["config"]
    assert branch["default_handle"] == "nur_ablegen"
    guard = branch["branches"][0]["guard"]
    assert guard == {"!=": [{"var": "input.auftrag"}, ""]}, "it reads what the button asked"
    assert (nodes["assistent"]["data"]["config"]["action"]["action"] == "assistant_task")
