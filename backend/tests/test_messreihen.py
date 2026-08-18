"""Messreihen: aus Zahlen Schlüsse ziehen.

Ein Ablauf sah bisher nur den Augenblick. „Akku 25 %" ist für sich belanglos — erst die
Reihe sagt, ob das Gerät in zwei Tagen oder in zwei Monaten stehenbleibt. Geprüft wird
hier beides: dass die Gerade stimmt, und dass die Vorwarnung genau einmal kommt (bei
einem Gerät, das täglich meldet, wäre eine tägliche Warnung dieselbe Warnung wie keine).
"""
import datetime as dt

import pytest
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services import metrics
from app.services.workflow_actions import run_action

from conftest import make_user

pytestmark = pytest.mark.asyncio


def _tage(n: float) -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=n)


async def _akku_verlauf(db, owner, werte):
    """werte = [(tage_zurück, wert)] — der jüngste zuletzt."""
    for zurueck, wert in werte:
        await metrics.erfassen(db, owner.id, "akku.shelter", wert, einheit="%", ts=_tage(zurueck))
    await db.commit()
    return await metrics.reihe(db, owner.id, "akku.shelter")


async def test_gerade_trifft_den_echten_verlauf(db):
    """Der reale Verlauf des Trackers: 65 % am 27.07., 25 % am 18.08. — 1,8 %/Tag."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (18, 57), (14, 50), (10, 46), (6, 37),
                                       (2, 27), (0, 25)])
    stand = await metrics.trend(db, r, ziel=0.0)
    assert stand["punkte"] == 7
    assert -2.0 < stand["pro_tag"] < -1.6, stand
    assert 12 < stand["rest_tage"] < 16, stand      # rund zwei Wochen
    assert stand["guete"] > 0.97


async def test_zu_wenige_punkte_ergeben_keine_prognose(db):
    """Zwei Messungen sind keine Reihe — lieber nichts sagen als eine Zufallszahl."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(2, 50), (0, 40)])
    stand = await metrics.trend(db, r)
    assert stand["rest_tage"] is None and stand["pro_tag"] is None


async def test_steigende_reihe_hat_kein_ende(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(4, 20), (3, 30), (2, 40), (0, 60)])
    stand = await metrics.trend(db, r, ziel=0.0)
    assert stand["pro_tag"] > 0 and stand["rest_tage"] is None


async def test_vorwarnung_kommt_genau_einmal(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (0, 25)])
    stand = await metrics.trend(db, r, ziel=0.0)
    assert metrics.vorwarnen(r, stand["rest_tage"], 20) is True
    assert metrics.vorwarnen(r, stand["rest_tage"], 20) is False, "nicht zweimal dasselbe"


async def test_neuer_akku_darf_wieder_warnen(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (0, 25)])
    metrics.vorwarnen(r, 5.0, 7)
    assert r.warned_at is not None
    # Deutlicher Anstieg = aufgefüllt: die Marke verfällt.
    await metrics.erfassen(db, anna.id, "akku.shelter", 100.0)
    await db.commit()
    assert r.warned_at is None
    assert metrics.vorwarnen(r, 5.0, 7) is True


async def _instanz(db, anna) -> WorkflowInstance:
    d = WorkflowDefinition(project_id=None, key="mess", name="Mess", created_by=anna.id,
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id,
                            subject_kind=WorkflowSubjectKind.standalone,
                            context={"position": {"attributes": {"batteryLevel": 25}}},
                            started_by=anna.id)
    db.add(inst)
    await db.flush()
    return inst


async def test_aktion_schreibt_und_liest_ab(db):
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37)])
    inst = await _instanz(db, anna)
    node = {"id": "mess", "type": "auto_action", "data": {"config": {"action": {
        "action": "messwert", "params": {
            "reihe": "akku.shelter", "wert": "{{ position.attributes.batteryLevel }}",
            "einheit": "%", "vorwarn_tage": 20}}}}}
    r = await run_action(db, inst, node)
    assert r["wert"] == 25.0 and r["rest_tage"] and r["warnen"] is True
    stand = inst.context["messreihe"]
    assert stand["einheit"] == "%" and stand["leer_am"]


async def test_aktion_duldet_prozentzeichen_und_komma(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "messwert", "params": {"reihe": "x", "wert": "12,5 %"}}}}}
    assert (await run_action(db, inst, node))["wert"] == 12.5


async def test_aktion_meckert_bei_unsinn(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "messwert", "params": {"reihe": "x", "wert": "keine Zahl"}}}}}
    with pytest.raises(ValueError):
        await run_action(db, inst, node)


async def test_unsinnige_werte_kommen_nicht_in_die_reihe(db):
    """Der Tracker meldet `batteryLevel: 127`, wenn er den Ladestand nicht kennt.

    Ein einziger solcher Punkt verbiegt die Gerade so, dass aus „in zwei Wochen leer"
    ein „steigt leicht an" wird — genau das ist beim ersten echten Ereignis passiert.
    """
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (0, 25)])
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "messwert", "params": {"reihe": "akku.shelter", "wert": 127,
                                         "min": 0, "max": 100}}}}}
    r = await run_action(db, inst, node)
    assert r["ignoriert"] is True
    r2 = await metrics.reihe(db, anna.id, "akku.shelter")
    assert r2.last_value == 25.0, "der letzte echte Wert bleibt stehen"
    stand = await metrics.trend(db, r2)
    assert stand["punkte"] == 4 and stand["pro_tag"] < 0

    # Der Lauf bricht nicht ab — die nächste Weiche darf das erkennen.
    assert inst.context["messreihe"]["ignoriert"] is True
    assert inst.context["messreihe"]["warnen"] is False


async def test_werte_aus_wenigen_minuten_ergeben_keinen_tagestrend(db):
    """Vier Spannungswerte aus drei Minuten ergaben „+14 V pro Tag" — Rauschen, hochgerechnet."""
    anna = await make_user(db, "anna")
    for minuten, wert in [(6, 3.50), (4, 3.50), (2, 3.50), (0, 3.52)]:
        await metrics.erfassen(db, anna.id, "spannung", wert, einheit="V",
                               ts=dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(minutes=minuten))
    await db.commit()
    r = await metrics.reihe(db, anna.id, "spannung")
    stand = await metrics.trend(db, r, ziel=3.2)
    assert stand["punkte"] == 4 and stand["pro_tag"] is None and stand["rest_tage"] is None


async def test_fehlender_wert_wird_uebersprungen(db):
    """Ein Ereignis ohne Messung ist kein Defekt.

    Der Tracker meldet „bin ausgefallen" ohne Position — und damit ohne Ladestand. Vorher
    scheiterte der Schritt und stand rot im Protokoll, obwohl nichts kaputt war.
    """
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(6, 40), (3, 34), (0, 30)])
    inst = await _instanz(db, anna)
    inst.context = {"event": {"type": "deviceInactive"}}      # keine position im Payload
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "messwert", "params": {
            "reihe": "akku.shelter", "wert": "{{ position.attributes.batteryLevel }}"}}}}}
    r = await run_action(db, inst, node)
    assert r["uebersprungen"] is True and r["ignoriert"] is True
    assert inst.context["messreihe"]["warnen"] is False
    # Der letzte bekannte Stand bleibt stehen — bei einer Ausfallmeldung ist er die
    # interessanteste Zahl, die es noch gibt.
    assert inst.context["messreihe"]["wert"] == 30.0
    reihe = await metrics.reihe(db, anna.id, "akku.shelter")
    assert reihe.last_value == 30.0
    assert (await metrics.trend(db, reihe))["punkte"] == 3, "kein Punkt dazugekommen"


async def test_pflichtwert_bleibt_ein_fehler(db):
    """Wo der Wert der Zweck des Schritts ist, soll sein Fehlen auffallen."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "messwert", "params": {"reihe": "x", "wert": "", "pflicht": True}}}}}
    with pytest.raises(ValueError):
        await run_action(db, inst, node)


# ── Stille: wenn gar nichts mehr kommt ───────────────────────────────────────

async def test_trend_nennt_das_alter_des_letzten_werts(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    stand = await metrics.trend(db, r)
    assert 71 < stand["alter_stunden"] < 73        # drei Tage
    assert stand["letzter_am"]


async def test_alter_auch_ohne_gerade(db):
    """Gerade zwei Werte — für eine Prognose zu wenig, fürs Alter reicht es."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(5, 50), (4, 45)])
    stand = await metrics.trend(db, r)
    assert stand["pro_tag"] is None and stand["alter_stunden"] > 90


async def test_stille_wird_genau_einmal_gemeldet(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    alter = (await metrics.trend(db, r))["alter_stunden"]
    assert metrics.stille_melden(r, alter, 26) is True
    assert metrics.stille_melden(r, alter, 26) is False, "ein stündlicher Wächter darf nicht nerven"


async def test_neuer_wert_beendet_die_stille(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    metrics.stille_melden(r, 72.0, 26)
    assert r.still_at is not None
    await metrics.erfassen(db, anna.id, "akku.shelter", 39.0)   # auch ein schlechter Wert zählt
    await db.commit()
    assert r.still_at is None
    assert metrics.stille_melden(r, 72.0, 26) is True


async def test_stille_und_restlaufzeit_verschlucken_sich_nicht(db):
    """Zwei Tatsachen, zwei Marken — sonst frisst eine Meldung die andere."""
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(22, 65), (14, 50), (6, 37), (3, 25)])
    stand = await metrics.trend(db, r, ziel=0.0)
    assert metrics.vorwarnen(r, stand["rest_tage"], 30) is True
    assert metrics.stille_melden(r, stand["alter_stunden"], 26) is True
    assert r.warned_at is not None and r.still_at is not None


async def test_aktion_liest_ohne_zu_schreiben(db):
    anna = await make_user(db, "anna")
    r = await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "messreihe_lesen",
        "params": {"reihe": "akku.shelter", "still_stunden": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["still"] is True and erg["still_melden"] is True
    assert (await metrics.trend(db, r))["punkte"] == 3, "es darf kein Punkt entstehen"
    stand = inst.context["messreihe"]
    assert stand["gefunden"] is True and stand["wert"] == 40.0


async def test_unbekannte_reihe_ist_kein_fehler(db):
    """Ein Tippfehler im Schlüssel wäre sonst jede Stunde ein roter Lauf."""
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "messreihe_lesen", "params": {"reihe": "gibt.es.nicht",
                                                "still_stunden": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["gefunden"] is False
    assert inst.context["messreihe"]["still_melden"] is False


async def test_frische_reihe_schweigt_nicht(db):
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(0.2, 80), (0.1, 79), (0, 78)])
    inst = await _instanz(db, anna)
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "messreihe_lesen", "params": {"reihe": "akku.shelter",
                                                "still_stunden": 26}}}}}
    erg = await run_action(db, inst, node)
    assert erg["still"] is False and erg["still_melden"] is False


async def test_zahlen_duerfen_aus_dem_kontext_kommen(db):
    """Derselbe Wächter für mehrere Reihen: Schwelle und Fenster kommen vom Job.

    Vorher scheiterte der Schritt an „{{ still_stunden }}" — einem Text, der wie eine Zahl
    gemeint war.
    """
    anna = await make_user(db, "anna")
    await _akku_verlauf(db, anna, [(9, 60), (6, 50), (3, 40)])
    inst = await _instanz(db, anna)
    inst.context = {"reihe": "akku.shelter", "grenze": 26}
    node = {"id": "lesen", "type": "auto_action", "data": {"config": {"action": {
        "action": "messreihe_lesen",
        "params": {"reihe": "{{ reihe }}", "still_stunden": "{{ grenze }}"}}}}}
    erg = await run_action(db, inst, node)
    assert erg["still"] is True and erg["still_melden"] is True


async def test_grenzen_duerfen_aus_dem_kontext_kommen(db):
    anna = await make_user(db, "anna")
    inst = await _instanz(db, anna)
    inst.context = {"obergrenze": 100, "roh": 127}
    node = {"id": "m", "type": "auto_action", "data": {"config": {"action": {
        "action": "messwert", "params": {"reihe": "x", "wert": "{{ roh }}",
                                         "min": 1, "max": "{{ obergrenze }}"}}}}}
    assert (await run_action(db, inst, node))["ignoriert"] is True
