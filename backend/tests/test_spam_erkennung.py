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
    adressen_aus_notiz, bekannte_domains, namens_kollision, sync_contacts,
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

async def test_saubere_mail_ohne_verdacht():
    res = evaluate(_mail(), meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert res.score == 0.0
    assert res.sender_email == "info@shop.de"
    assert res.signals == []


async def test_faelschungsmuster_schlaegt_an():
    """SPF failed, a foreign return path, a foreign reply address, a disguised name."""
    res = evaluate(_mail(
        **{"from": [{"name": "DHL Zustellung <service@dhl.de>", "addr": "x@dhl-tracking.xyz"}],
           "reply_to": [{"name": "", "addr": "kasse@4t7k.ru"}],
           "headers": {
               "Authentication-Results": "mx; spf=fail; dkim=fail; dmarc=fail",
               "Return-Path": "<bounce@4t7k.ru>",
               "Received-Count": 1,
           }}),
        meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert "spf_fail" in res.signals
    assert "dmarc_fail" in res.signals
    assert "returnpath_mismatch" in res.signals
    assert "replyto_fremd" in res.signals
    assert "absender_name_taeuscht" in res.signals
    assert "billig_tld" in res.signals
    assert res.score >= 0.9
    assert not res.ist_newsletter


async def test_bounce_unterdomaene_ist_kein_mismatch():
    """`bounce.shop.de` to `shop.de` is usual and must raise no suspicion."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<b@bounce.shop.de>", "Received-Count": 3}))
    assert "returnpath_mismatch" not in res.signals


async def test_newsletter_bleibt_newsletter():
    """Clean bulk sending with an unsubscribe path is not spam; otherwise order confirmations
    disappear into the spam folder."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>",
        "List-Unsubscribe": "<https://shop.de/abmelden>",
        "Precedence": "bulk", "Received-Count": 3}))
    assert res.ist_newsletter
    assert res.score == 0.0


async def test_massenversand_ohne_abmeldeweg():
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass", "Precedence": "bulk", "Received-Count": 3}))
    assert "kein_unsubscribe_bei_bulk" in res.signals
    assert not res.ist_newsletter


async def test_blindkopie_versand_faellt_auf():
    """One's own address stands nowhere, which is typical of bulk sending by blind copy."""
    res = evaluate(_mail(to=[{"name": "", "addr": "irgendwer@example.org"}]),
                   meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert "bcc_blast" in res.signals


async def test_eigene_domain_als_platzhalter():
    """Whoever receives a whole domain cannot enumerate their aliases."""
    res = evaluate(_mail(to=[{"name": "", "addr": "shop-alias@meine-domain.de"}]),
                   meine_adressen=frozenset({"*@meine-domain.de"}))
    assert "bcc_blast" not in res.signals


async def test_alias_wird_als_merkmal_gefuehrt():
    """The addressed alias is a signal of its own: an alias only one provider knows and that
    suddenly receives foreign advertising has been sold."""
    res = evaluate(_mail(to=[{"name": "", "addr": "shop-alias@meine-domain.de"}]))
    merkmale = features(res, "Ihre Bestellung")
    assert "to:shop-alias@meine-domain.de" in merkmale
    assert "from:info@shop.de" in merkmale
    assert "dom:shop.de" in merkmale


async def test_freemail_domain_ist_kein_merkmal():
    """Everybody hangs off gmx.de, so the domain says nothing."""
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "wer@gmx.de"}]}))
    merkmale = features(res, "Hallo")
    assert "dom:gmx.de" not in merkmale
    assert "from:wer@gmx.de" in merkmale


# --- Echtheit: Ausrichtung ------------------------------------------------------------

async def test_dkim_pass_von_fremder_domain_faellt_auf():
    """A valid signature only says that SOMEBODY signed. Only the alignment with the sender
    domain makes a statement of it, and that is the most common way of forging with a "DKIM
    pass" anyway."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=none",
        "Return-Path": "<bounce@shop.de>",
        "DKIM-Domains": ["versender-xy.top"], "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" in res.signals
    assert not res.ist_newsletter


async def test_bestandenes_dmarc_beendet_die_ausrichtungsfrage():
    """DMARC IS the alignment check: if it passes, something is aligned by definition. Without
    this brake the rule fires on every mailing list contribution and every Google Workspace
    sender, which measured against real post is the most common false alarm of all."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>",
        "DKIM-Domains": ["shop-io.20251104.gappssmtp.com"], "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" not in res.signals


async def test_mailingliste_darf_gegensignieren():
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=none",
        "Return-Path": "<bounce@shop.de>", "List-Id": "<xiegu.groups.io>",
        "List-Unsubscribe": "<https://groups.io/ab>",
        "DKIM-Domains": ["groups.io"], "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" not in res.signals


async def test_dkim_unterdomaene_ist_ausgerichtet():
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>",
        "DKIM-Domains": ["mail.shop.de"], "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" not in res.signals


async def test_dkim_domain_aus_authentication_results():
    """Without a `DKIM-Signature` in the payload, `header.d=` carries the same information."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass header.d=fremd.top; dmarc=none",
        "Return-Path": "<bounce@shop.de>", "Received-Count": 3}))
    assert "dkim_nicht_ausgerichtet" in res.signals


# --- Verdict of one's own mail server (learned from real mailboxes) --------------------

async def test_spam_level_sterne_werden_gelesen():
    """`X-Spam-Level: **************`, one star per point. The server has long assessed it,
    and ignoring that and rebuilding it oneself would be the worse copy."""
    res = evaluate(_mail(headers={"X-Spam-Level": "**************", "Received-Count": 3}))
    assert "server_spam_hoch" in res.signals
    assert res.score >= 0.7


async def test_mittlere_serverbewertung():
    res = evaluate(_mail(headers={"X-Spam-Status": "No, score=6.2 tests=[…]",
                                  "Received-Count": 3}))
    assert "server_spam_mittel" in res.signals


async def test_spam_markierung_im_betreff():
    res = evaluate(_mail(subject="***SPAM*** Erste-Hilfe-Set anfordern"))
    assert "betreff_spam_markiert" in res.signals


async def test_serverurteil_schlaegt_die_newsletter_bremse():
    """An unsubscribe button does not turn recognised rubbish into a subscribed newsletter."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>", "X-Spam-Level": "***********",
        "List-Unsubscribe": "<https://shop.de/ab>", "Received-Count": 3}))
    assert not res.ist_newsletter


# --- Forwarding, self-forgery, click counters (learned from real mailboxes) -------------

async def test_srs_rueckweg_ist_kein_verdacht():
    """On forwarding, one's own server rewrites the return path onto itself. Then one's own
    domain ALWAYS stands there, and a signal that fires on every mail is none."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<SRS0=m6Wz=GK=shop.de=info@meine-domain.de>", "Received-Count": 3}),
        meine_adressen=frozenset({"*@meine-domain.de"}))
    assert "returnpath_mismatch" not in res.signals


async def test_verp_bounceadresse_loest_nicht_aus():
    """Serious sending services bounce over addresses of their own (`bounces+…-kickstarter@…`).
    From a forwarded mail there is therefore nothing to get from the return path."""
    res = evaluate(_mail(headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<SRS0=YnP1=GK=bounce.dienst.de=bounces+145129-0b4c-shop.de@meine-domain.de>",
        "Received-Count": 3}),
        meine_adressen=frozenset({"*@meine-domain.de"}))
    assert "returnpath_mismatch" not in res.signals


async def test_mail_von_meiner_eigenen_adresse():
    """"From you to you" without a passed check: the oldest trick."""
    res = evaluate(_mail(**{"from": [{"name": "Ich", "addr": "ich@meine-domain.de"}],
                            "headers": {"Received-Count": 1}}),
                   meine_adressen=frozenset({"*@meine-domain.de"}))
    assert "absender_bin_ich" in res.signals


async def test_echte_eigene_post_bleibt_unverdaechtig():
    res = evaluate(_mail(**{"from": [{"name": "Ich", "addr": "ich@meine-domain.de"}],
                            "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                                        "Return-Path": "<ich@meine-domain.de>",
                                        "Received-Count": 3}}),
                   meine_adressen=frozenset({"*@meine-domain.de"}))
    assert "absender_bin_ich" not in res.signals


async def test_klickzaehler_ist_keine_taeuschung():
    """Newsletters route EVERY link over a counting service; that is the normal case."""
    res = evaluate(_mail(links=[{"href": "https://ctrk.klclick.com/x", "text": "commodore.net"}]))
    assert "link_text_taeuscht" not in res.signals


async def test_ziel_auf_der_absenderdomain_ist_keine_taeuschung():
    """`emails.kickstarter.com` with the sender `kickstarter.com`: the same yard."""
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "no-reply@kickstarter.com"}],
                            "links": [{"href": "https://emails.kickstarter.com/x",
                                       "text": "www.youtube.com"}]}))
    assert "link_text_taeuscht" not in res.signals


async def test_ziel_das_ich_kenne_verbirgt_nichts():
    res = evaluate(_mail(links=[{"href": "https://www.amazon.de/x", "text": "dpd.de"}]),
                   bekannte_domains=frozenset({"amazon.de"}))
    assert "link_text_taeuscht" not in res.signals


async def test_unbekannte_umleitung_in_einer_liste_ist_nur_ein_punkt():
    res = evaluate(_mail(links=[{"href": "https://s8493.mjt99.example/x", "text": "eon.de"}],
                         headers={"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                                  "Return-Path": "<bounce@shop.de>",
                                  "List-Unsubscribe": "<https://eon.de/ab>",
                                  "Received-Count": 3}))
    assert "link_text_umgeleitet" in res.signals
    assert "link_text_taeuscht" not in res.signals


# --- Deception in the name --------------------------------------------------------------

async def test_punycode_absender():
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@xn--spakasse-9db.de"}]}))
    assert "punycode_absender" in res.signals


async def test_kyrillisches_o_im_absender():
    """"sparkasse" with a Cyrillic "а" looks identical but is a different domain."""
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@spаrkasse.de"}]}))
    assert "schriftmischung" in res.signals


async def test_unsichtbare_zeichen_im_betreff():
    res = evaluate(_mail(subject="Ihre Rech​nung ist fällig"))
    assert "unsichtbare_zeichen" in res.signals


async def test_bekannte_marke_als_fremde_subdomain():
    """`sparkasse.de.sicherheit.top` pulls a known brand into the visible address without
    owning it."""
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@sparkasse.de.sicherheit.top"}]}),
                   bekannte_domains=frozenset({"sparkasse.de"}))
    assert "marke_als_subdomain" in res.signals


async def test_bekannte_marke_mit_bindestrich():
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@sparkasse-de.top"}]}),
                   bekannte_domains=frozenset({"sparkasse.de"}))
    assert "marke_als_subdomain" in res.signals


async def test_echte_bekannte_domain_ist_kein_missbrauch():
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "info@sparkasse.de"}]}),
                   bekannte_domains=frozenset({"sparkasse.de"}))
    assert "marke_als_subdomain" not in res.signals


# --- Kopfzeilen-Hygiene ---------------------------------------------------------------

async def test_fehlende_message_id():
    res = evaluate(_mail(message_id=None))
    assert "msgid_fehlt" in res.signals


async def test_vorgetaeuschte_antwort():
    """"Re:" without a reference is an answer to a conversation that never existed."""
    res = evaluate(_mail(subject="Re: Ihre offene Rechnung", message_id="<a@shop.de>"))
    assert "fake_antwort" in res.signals


async def test_echte_antwort_ist_in_ordnung():
    res = evaluate(_mail(subject="Re: Ihre Bestellung", message_id="<a@shop.de>", headers={
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "Return-Path": "<bounce@shop.de>", "In-Reply-To": "<vorher@shop.de>",
        "Received-Count": 3}))
    assert "fake_antwort" not in res.signals


async def test_geschrienener_und_gestreckter_betreff():
    res = evaluate(_mail(subject="G.E.W.I.N.N.S.P.I.E.L!!!", message_id="<a@shop.de>"))
    assert "betreff_gestreckt" in res.signals
    assert "betreff_geschrien" in res.signals


async def test_zufaellige_absenderadresse():
    res = evaluate(_mail(**{"from": [{"name": "", "addr": "a7f3c9e21b84d6f0@shop.de"}]}))
    assert "absender_zufaellig" in res.signals


async def test_datum_in_der_zukunft():
    res = evaluate(_mail(date="2026-08-20T10:00:00+02:00",
                         timestamp="2026-08-17T10:00:00+02:00"))
    assert "datum_versatz" in res.signals


async def test_aufgesetzte_dringlichkeit():
    res = evaluate(_mail(headers={"X-Priority": "1", "Received-Count": 3}))
    assert "aufgesetzte_dringlichkeit" in res.signals


# --- Links ------------------------------------------------------------------------------

async def test_linktext_zeigt_andere_domain_als_das_ziel():
    """The most reliable phishing indicator of all: in honest post it practically does not
    occur."""
    res = evaluate(_mail(links=[{"href": "https://4t7k.ru/login", "text": "www.paypal.de"}]))
    assert "link_text_taeuscht" in res.signals
    assert any("4t7k.ru" in g for g in res.reasons)


async def test_linktext_ohne_domainangabe_behauptet_nichts():
    res = evaluate(_mail(links=[{"href": "https://shop.de/x", "text": "Hier klicken"}]))
    assert "link_text_taeuscht" not in res.signals


async def test_linktext_gleiche_wurzel_ist_in_ordnung():
    res = evaluate(_mail(links=[{"href": "https://tracking.shop.de/x", "text": "shop.de"}]))
    assert "link_text_taeuscht" not in res.signals


async def test_link_auf_nackte_ip():
    res = evaluate(_mail(links=[{"href": "http://192.0.2.5/login", "text": "Konto prüfen"}]))
    assert "link_ip_adresse" in res.signals


async def test_at_trick_im_link():
    """Everything before the @ is decoration, the target is boese.top."""
    res = evaluate(_mail(links=[{"href": "https://paypal.de@boese.top/login", "text": "Konto"}]))
    assert "link_at_trick" in res.signals


async def test_kuerzungsdienst_und_billige_endung():
    res = evaluate(_mail(links=[{"href": "https://bit.ly/x", "text": "mehr"},
                                {"href": "https://gewinn.top/y", "text": "hier"}]))
    assert "link_kuerzungsdienst" in res.signals
    assert "link_billig_tld" in res.signals


# --- Attachments ---------------------------------------------------------------------------

async def test_ausfuehrbarer_anhang():
    res = evaluate(_mail(attachments=[{"filename": "Rechnung.exe", "content_type": "x", "size_bytes": 1}]))
    assert "anhang_ausfuehrbar" in res.signals


async def test_doppelendung():
    res = evaluate(_mail(attachments=[{"filename": "Rechnung.pdf.exe"}]))
    assert "anhang_doppelendung" in res.signals
    assert "anhang_ausfuehrbar" in res.signals


async def test_svg_anhang_ist_eigener_leichter_posten():
    """SVG and HTML are sent as disguised login masks, but Google attaches its terms of use
    as .html as well. Hence an own, light entry instead of a common pot with executable
    files."""
    res = evaluate(_mail(attachments=[{"filename": "Dokument.svg"}]))
    assert "anhang_webseite" in res.signals
    assert "anhang_ausfuehrbar" not in res.signals
    assert res.score < 0.2


async def test_harmloser_anhang_bleibt_harmlos():
    res = evaluate(_mail(attachments=[{"filename": "Rechnung_2026.pdf"}]))
    assert "anhang_ausfuehrbar" not in res.signals


async def test_passwortgeschuetztes_archiv():
    """An archive whose password stands in the text is blind to every scanner."""
    res = evaluate(_mail(attachments=[{"filename": "Unterlagen.zip"}]),
                   body="Das Passwort lautet 1234.")
    assert "anhang_archiv_mit_passwort" in res.signals


async def test_archiv_ohne_passwort_im_text():
    res = evaluate(_mail(attachments=[{"filename": "Unterlagen.zip"}]), body="Anbei die Unterlagen.")
    assert "anhang_archiv_mit_passwort" not in res.signals


# --- Mail text from varying fields ---------------------------------------------------------

async def test_mailtext_findet_jedes_feld():
    """The watcher delivers `body_text` OR `body_html_as_text`; whoever reads only `body`
    assesses an empty text for half of all mails."""
    assert mail_text({"body_text": "a"}) == "a"
    assert mail_text({"body_html_as_text": "b"}) == "b"
    assert mail_text({"body": "c"}) == "c"
    assert mail_text({}) == ""


# --- Vault-Kontakte ------------------------------------------------------------------

async def test_adressen_aus_notiz_trennt_herkunft():
    notiz = (
        "---\n"
        "tags:\n  - kontakt\n"
        "email: rainer@t-online.de\n"
        "email_afu:\n"
        "  - dl1abc@verband.de\n"
        "telefon: '+49123'\n"
        "---\n\n"
        "# Rainer\n\nSchrieb mir von buero@firma.de aus.\n"
    )
    gefunden = dict(adressen_aus_notiz(notiz))
    assert gefunden["rainer@t-online.de"] == "frontmatter"
    assert gefunden["dl1abc@verband.de"] == "frontmatter"
    assert gefunden["buero@firma.de"] == "body"


async def test_beispieladressen_werden_ausgelassen():
    notiz = "---\nemail: max@example.com\n---\n\nText\n"
    assert adressen_aus_notiz(notiz) == []


async def test_vault_abgleich_spiegelt(db, tmp_path):
    ordner = tmp_path / "03 Bereiche" / "Kontakte"
    ordner.mkdir(parents=True)
    (ordner / "Rainer.md").write_text("---\nemail: rainer@t-online.de\n---\n", encoding="utf-8")
    user = await make_user(db, "dennis")

    await sync_contacts(db, user.id, str(tmp_path))
    rows = (await db.execute(select(AssistantContact))).scalars().all()
    assert [r.email for r in rows] == ["rainer@t-online.de"]
    assert rows[0].domain == "t-online.de"

    # The note is gone, so the entry is gone (a mirror, not a stock of its own).
    (ordner / "Rainer.md").write_text("---\nemail: neu@t-online.de\n---\n", encoding="utf-8")
    await sync_contacts(db, user.id, str(tmp_path))
    rows = (await db.execute(select(AssistantContact))).scalars().all()
    assert [r.email for r in rows] == ["neu@t-online.de"]


async def test_leerer_vault_raeumt_nicht_ab(db, tmp_path):
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

async def _kontakt(db, owner_id, name: str, email: str) -> None:
    db.add(AssistantContact(owner_user_id=owner_id, email=email, name=name,
                            domain=email.split("@", 1)[1], source_kind="frontmatter"))
    await db.commit()


async def test_bekannter_name_von_fremder_adresse(db):
    """No link, no attachment, no technical forgery: only a borrowed name. Only the contact
    stock gives that away."""
    user = await make_user(db, "dennis")
    await _kontakt(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    assert await namens_kollision(db, user.id, "Rainer Beispiel", "r.beispiel@gmx-mail.top") \
        == "Rainer Beispiel"


async def test_derselbe_mensch_ist_keine_kollision(db):
    user = await make_user(db, "dennis")
    await _kontakt(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    assert await namens_kollision(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de") == ""


async def test_anrede_und_umgedrehte_schreibweise(db):
    user = await make_user(db, "dennis")
    await _kontakt(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    assert await namens_kollision(db, user.id, "Herr Dr. Rainer Beispiel", "x@fremd.top")
    assert await namens_kollision(db, user.id, "Beispiel, Rainer", "x@fremd.top")


async def test_einteiliger_name_loest_nicht_aus(db):
    """"Info" or "support" are not people, and a single part name would match by chance
    constantly."""
    user = await make_user(db, "dennis")
    await _kontakt(db, user.id, "Support", "support@shop.de")
    assert await namens_kollision(db, user.id, "Support", "support@fremd.top") == ""


async def test_bekannte_domains_ohne_fliesstext(db):
    user = await make_user(db, "dennis")
    await _kontakt(db, user.id, "Rainer", "rainer@sparkasse.de")
    db.add(AssistantContact(owner_user_id=user.id, email="wer@zufall.top",
                            domain="zufall.top", source_kind="body"))
    await db.commit()
    domains = await bekannte_domains(db, user.id)
    assert "sparkasse.de" in domains
    assert "zufall.top" not in domains


async def test_chef_masche_erzeugt_verdacht(db):
    """End to end: a technically impeccable mail that pretends to be an acquaintance.

    The borrowed name alone deliberately carries no verdict (acquaintances write from their
    second address as well); only together with a redirected reply and manufactured urgency
    does it become a question.
    """
    user = await _owner(db)
    await _kontakt(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    urteil = await spam_review.beurteilen(db, user.id, _mail(
        **{"from": [{"name": "Rainer Beispiel", "addr": "r.beispiel.buero@gmail.com"}],
           "reply_to": [{"name": "", "addr": "kasse@zahlung-xy.top"}],
           "subject": "Dringend: kurze Bitte",
           "headers": {"Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
                       "X-Priority": "1", "Received-Count": 3}}),
        cls={"spam_score": 0.5, "category": "privat"})
    assert urteil["score"] >= urteil["frage_ab"]
    assert any("Rainer Beispiel" in g for g in urteil["reasons"])


async def test_geliehener_name_allein_traegt_kein_urteil(db):
    """An acquaintance writing from their second address must not pass as fraud: the vault
    never knows all the addresses of a person."""
    user = await _owner(db)
    await _kontakt(db, user.id, "Rainer Beispiel", "r.beispiel@t-online.de")
    urteil = await spam_review.beurteilen(db, user.id, _mail(
        **{"from": [{"name": "Rainer Beispiel", "addr": "rainer.beispiel@gmx.de"}],
           "subject": "Bilder von gestern"}),
        cls={"spam_score": 0.1, "category": "privat"})
    assert urteil["score"] < urteil["frage_ab"]


# --- Memory ---------------------------------------------------------------------------

async def _urteil(db, owner_id, merkmale, **over) -> SpamVerdict:
    felder = {"sender_email": "wer@spam.xyz", "subject": "Gewinn", **over}
    v = SpamVerdict(owner_user_id=owner_id, features=merkmale, **felder)
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v


async def test_entscheidungen_wirken_auf_die_naechste_mail(db):
    """The core: what the human decides has to change future assessments."""
    user = await make_user(db, "dennis")
    merkmale = ["from:wer@spam.xyz", "dom:spam.xyz", "wort:gewinn"]

    vorher, _, sicher = await spam_learn.bewerten(db, user.id, merkmale)
    assert vorher == 0.5 and not sicher      # no opinion without an observation

    for _ in range(3):
        v = await _urteil(db, user.id, merkmale)
        await spam_learn.merken(db, v, True)
        v.status = "spam"
    await db.commit()

    nachher, gruende, sicher = await spam_learn.bewerten(db, user.id, merkmale)
    assert nachher > vorher
    assert sicher, "a sender decided unanimously three times counts as resolved"
    assert any("3× Spam" in g for g in gruende)


async def test_erwuenschter_absender_wird_gelernt(db):
    user = await make_user(db, "dennis")
    merkmale = ["from:news@verband.de", "dom:verband.de"]
    for _ in range(3):
        v = await _urteil(db, user.id, merkmale)
        await spam_learn.merken(db, v, False)
        v.status = "ham"
    await db.commit()

    score, _, sicher = await spam_learn.bewerten(db, user.id, merkmale)
    assert score < 0.5 and sicher


async def test_umentscheiden_nimmt_die_alte_zaehlung_zurueck(db):
    """An error must not stay in the memory forever."""
    user = await make_user(db, "dennis")
    merkmale = ["from:news@verband.de"]
    v = await _urteil(db, user.id, merkmale)
    await spam_learn.merken(db, v, True)
    v.status = "spam"
    await db.commit()

    await spam_learn.merken(db, v, False, vorher="spam")
    await db.commit()
    from app.models.assistant import SpamFeatureStat
    row = (await db.execute(select(SpamFeatureStat).where(
        SpamFeatureStat.feature == "from:news@verband.de"))).scalar_one()
    assert (row.spam_count, row.ham_count) == (0, 1)


async def test_beispiele_fuer_den_prompt(db):
    user = await make_user(db, "dennis")
    v = await _urteil(db, user.id, ["from:wer@spam.xyz"])
    await spam_review.entscheiden(db, v, True, decided_by="telegram")
    zeilen = await spam_learn.beispiele(db, user.id)
    assert zeilen and "SPAM" in zeilen[0]


# --- Beurteilung im Ganzen -----------------------------------------------------------

async def _owner(db):
    user = await make_user(db, "dennis")
    user.telegram_chat_id = "4242"
    await db.commit()
    return user


async def test_bekannter_kontakt_wird_in_ruhe_gelassen(db):
    user = await _owner(db)
    db.add(AssistantContact(owner_user_id=user.id, email="info@shop.de", domain="shop.de",
                            source_kind="frontmatter"))
    await db.commit()
    urteil = await spam_review.beurteilen(db, user.id, _mail(),
                                          cls={"spam_score": 0.8, "category": "werbung"})
    assert urteil["bekannter_kontakt"] is True


async def test_gefaelschter_bekannter_kontakt_faellt_auf(db):
    """The known name is the rewarding target; here the whitelist must NOT take hold."""
    user = await _owner(db)
    db.add(AssistantContact(owner_user_id=user.id, email="info@shop.de", domain="shop.de",
                            source_kind="frontmatter"))
    await db.commit()
    urteil = await spam_review.beurteilen(db, user.id, _mail(headers={
        "Authentication-Results": "mx; spf=fail; dmarc=fail",
        "Return-Path": "<b@4t7k.ru>", "Received-Count": 1,
    }), cls={"spam_score": 0.5, "category": "sonstiges"})
    assert urteil["bekannter_kontakt"] is False
    assert urteil["score"] >= urteil["frage_ab"]
    assert any("Fälschungsverdacht" in g for g in urteil["reasons"])


async def test_hoher_verdacht_kommt_ueber_die_sofort_schwelle(db):
    """Whether that becomes an immediate card is decided by the flow; the height of the
    suspicion is decided here (see test_mail_intake_prozess.py)."""
    user = await _owner(db)
    urteil = await spam_review.beurteilen(db, user.id, _mail(
        **{"from": [{"name": "", "addr": "x@4t7k.xyz"}],
           "reply_to": [{"name": "", "addr": "kasse@anders.ru"}],
           "headers": {"Authentication-Results": "mx; spf=fail; dkim=fail; dmarc=fail",
                       "Return-Path": "<b@anders.ru>", "Received-Count": 1}}),
        cls={"spam_score": 0.95, "spam_reason": "Paket-Vorwand", "category": "spam"})
    assert urteil["score"] >= urteil["sofort_ab"]


async def test_mittlerer_verdacht_bleibt_unter_der_sofort_schwelle(db):
    user = await _owner(db)
    urteil = await spam_review.beurteilen(db, user.id, _mail(headers={
        "Authentication-Results": "mx; spf=softfail", "Received-Count": 1}),
        cls={"spam_score": 0.6, "category": "werbung"})
    assert urteil["frage_ab"] <= urteil["score"] < urteil["sofort_ab"]


async def test_unverdaechtiges_bleibt_unter_der_frage_schwelle(db):
    user = await _owner(db)
    urteil = await spam_review.beurteilen(db, user.id, _mail(),
                                          cls={"spam_score": 0.1, "category": "rechnung"})
    assert urteil["score"] < urteil["frage_ab"]


async def test_geklaerter_absender_wird_nicht_erneut_gefragt(db):
    """After three unanimous "wanted" the question should stop; that is the purpose of the
    learning."""
    user = await _owner(db)
    merkmale = features(evaluate(_mail(headers={"Authentication-Results": "mx; spf=softfail",
                                                "Received-Count": 1})), "Ihre Bestellung")
    for _ in range(3):
        v = await _urteil(db, user.id, merkmale, sender_email="info@shop.de")
        await spam_learn.merken(db, v, False)
        v.status = "ham"
    await db.commit()

    urteil = await spam_review.beurteilen(db, user.id, _mail(headers={
        "Authentication-Results": "mx; spf=softfail", "Received-Count": 1}),
        cls={"spam_score": 0.7, "category": "werbung"})
    # The memory pulls the verdict below the question threshold, so the same mail no longer
    # triggers a second question. (It does not count as "settled": a forgery signal, here the
    # failed SPF, deliberately lifts the acquittal out of the memory.)
    assert urteil["learned_score"] < 0.5
    assert urteil["score"] < urteil["frage_ab"]


# --- Decision plus execution -------------------------------------------------------

@pytest.fixture
def imap_stub(monkeypatch):
    """Replace `imap-mcp` by a transcript."""
    aufrufe = []

    async def fake_call_tool(url, tool, arguments, **kw):
        aufrufe.append((tool, arguments))
        return {"content": [{"type": "text", "text": "verschoben nach Spam"}]}

    monkeypatch.setattr(spam_review, "call_tool", fake_call_tool)
    return aufrufe


async def test_bestaetigung_verschiebt_und_lernt(db, imap_stub):
    user = await _owner(db)
    v = await _urteil(db, user.id, ["from:wer@spam.xyz", "wort:gewinn"],
                      account="privat", folder="INBOX", uid=4711)
    ergebnis = await spam_review.entscheiden(db, v, True)

    assert imap_stub == [("mark_spam", {"account": "privat", "folder": "INBOX", "uid": 4711})]
    assert "verschoben" in ergebnis
    assert v.status == "spam" and v.decided_by == "telegram"
    score, _, _ = await spam_learn.bewerten(db, user.id, ["from:wer@spam.xyz"])
    assert score > 0.5


async def test_ablehnung_merkt_den_absender_vor(db, imap_stub):
    """"Not spam" is more than a no: the sender should not stand out at all in future."""
    user = await _owner(db)
    v = await _urteil(db, user.id, ["from:news@verband.de"], account="privat", folder="INBOX",
                      uid=99)
    v.sender_email, v.sender_domain = "news@verband.de", "verband.de"
    await db.commit()

    await spam_review.entscheiden(db, v, False)
    assert imap_stub[0][0] == "mark_not_spam"
    regel = (await db.execute(select(AssistantPolicy).where(
        AssistantPolicy.match_value == "news@verband.de"))).scalar_one()
    assert regel.match_kind == "sender"


async def test_fehlgeschlagenes_verschieben_behaelt_die_entscheidung(db, monkeypatch):
    """IMAP briefly gone: the decision of the human was right regardless and stays in the
    memory; otherwise they would have to take it once more."""
    from app.services.mcp_client import McpError

    async def kaputt(*a, **k):
        raise McpError("Connection refused")

    monkeypatch.setattr(spam_review, "call_tool", kaputt)
    user = await _owner(db)
    v = await _urteil(db, user.id, ["from:wer@spam.xyz"], account="privat", folder="INBOX",
                      uid=1)
    ergebnis = await spam_review.entscheiden(db, v, True)
    assert ergebnis.startswith("nicht verschoben")
    assert v.status == "spam"
    score, _, _ = await spam_learn.bewerten(db, user.id, ["from:wer@spam.xyz"])
    assert score > 0.5


async def test_sammelkarte_buendelt_und_entscheidet(db, imap_stub):
    import datetime as dt

    user = await _owner(db)
    alt = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=5)
    for i in range(3):
        v = await _urteil(db, user.id, [f"from:wer{i}@spam.xyz"], account="privat",
                          folder="INBOX", uid=100 + i, score=0.6)
        v.created_at = alt
    await db.commit()

    assert await spam_review.digest_faellig(db) == 1
    karte = (await db.execute(select(Notification).where(
        Notification.kind == "spam_digest"))).scalar_one()
    assert "3 Spam-Verdachtsfälle" in karte.title

    erster = await db.get(SpamVerdict, karte.spam_verdict_id)
    anzahl, fehler = await spam_review.entscheide_batch(db, erster.digest_batch, True)
    assert (anzahl, fehler) == (3, 0)
    assert len(imap_stub) == 3
    offen = (await db.execute(select(SpamVerdict).where(
        SpamVerdict.status == "pending"))).scalars().all()
    assert offen == []


async def test_sammelkarte_ueberspringt_sofort_gemeldete(db):
    """A case that already has a single card must not additionally turn up in the digest;
    otherwise the same thing is asked twice."""
    import datetime as dt

    user = await _owner(db)
    v = await _urteil(db, user.id, ["from:wer@spam.xyz"], score=0.95)
    v.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=5)
    db.add(Notification(user_id=user.id, spam_verdict_id=v.id, kind="spam_review",
                        chat_id="4242", title="🚩", body="…"))
    await db.commit()

    assert await spam_review.digest_faellig(db) == 0


# --- Disguised bulk sending (a case from 2026-08-18) ---------------------------------

def _phish(**over) -> dict:
    """The domain invoice mail: technically almost clean, elaborate HTML without a single
    link, the unsubscribe path only in the header, addressed to a collective mailbox."""
    payload = _mail(
        **{"from": [{"name": "Kundenkonto Verwaltung", "addr": "admin@unbekannt.example"}],
           "to": [{"name": "", "addr": "fragen@mitmachverein.de"}],
           "subject": "Ihre Domain-Rechnung wartet auf Bearbeitung",
           "body_text": "Handlungsbedarf: Domain freifunk-ebs.de - Zahlungsinformation",
           "links": [],
           "headers": {"Authentication-Results": "strato.com; dmarc=pass; dkim=pass; "
                                                 "spf=softfail",
                       "List-Unsubscribe": "<mailto:bounce@unbekannt.example>",
                       "Return-Path": "<admin@unbekannt.example>", "Received-Count": 6}})
    payload.update(over)
    return payload


async def test_getarnter_massenversand_faellt_auf():
    res = evaluate(_phish(), meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert "abmeldung_nur_behauptet" in res.signals
    assert "geschaeft_an_rollenadresse" in res.signals
    # An unsubscribe path that does not exist in the body does not make a newsletter of it.
    assert res.ist_newsletter is False
    assert res.score >= 0.9


async def test_fassade_zaehlt_nur_bei_technikmangel():
    """Google Play, OpenAI and eQSL have no `<a href>` in the body and are genuine anyway:
    large senders unsubscribe over one-click in the header (RFC 8058). Whoever passes their
    checks may build their HTML as they like."""
    sauber = _phish(**{"headers": {
        "Authentication-Results": "mx; spf=pass; dkim=pass; dmarc=pass",
        "List-Unsubscribe": "<mailto:abmelden@unbekannt.example>",
        "Return-Path": "<bounce@unbekannt.example>", "Received-Count": 3}})
    res = evaluate(sauber, meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert "abmeldung_nur_behauptet" not in res.signals
    assert "html_ohne_links" not in res.signals


async def test_sammeladresse_ohne_geschaeftsvorgang_ist_harmlos():
    """Strangers write to `fragen@` constantly; that is the purpose of the address."""
    res = evaluate(_phish(subject="Frage zum nächsten Treffen",
                          body_text="Hallo, wann trefft ihr euch?"),
                   meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert "geschaeft_an_rollenadresse" not in res.signals


async def test_rechnung_an_persoenliche_adresse_ist_harmlos():
    res = evaluate(_phish(**{"to": [{"name": "", "addr": "ich@meine-domain.de"}]}),
                   meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert "geschaeft_an_rollenadresse" not in res.signals


async def test_buchhaltung_ist_keine_sammeladresse():
    """Some roles do have contracts, and the list separates that deliberately."""
    res = evaluate(_phish(**{"to": [{"name": "", "addr": "buchhaltung@verein.de"}]}),
                   meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert "geschaeft_an_rollenadresse" not in res.signals


async def test_geschaeftsfreie_domain_schlaegt_jede_adresse():
    """With some domains there is no contractual business, and there `vorstand@` or
    `buchhaltung@` is no exception either, because it simply does not exist. That is a
    statement of the human (`spam_keine_geschaeftsdomains`), not a heuristic."""
    ohne = frozenset({"mitmachverein.de"})
    for adresse in ("fragen@mitmachverein.de", "vorstand@mitmachverein.de",
                    "buchhaltung@mitmachverein.de", "michael@mitmachverein.de"):
        res = evaluate(_phish(**{"to": [{"name": "", "addr": adresse}]}),
                       meine_adressen=frozenset({"ich@meine-domain.de"}),
                       geschaeftsfreie_domains=ohne)
        assert "geschaeft_an_domain_ohne_geschaeft" in res.signals, adresse

    # Without that statement it stays with the weaker role heuristic.
    res = evaluate(_phish(**{"to": [{"name": "", "addr": "buchhaltung@mitmachverein.de"}]}),
                   meine_adressen=frozenset({"ich@meine-domain.de"}))
    assert "geschaeft_an_domain_ohne_geschaeft" not in res.signals
    assert "geschaeft_an_rollenadresse" not in res.signals


async def test_geschaeftsfreie_domain_ohne_geschaeftsvorgang_ist_harmlos():
    """The domain keeps receiving completely normal post, only no invoices."""
    res = evaluate(_phish(subject="Frage zum Treffen", body_text="Wann trefft ihr euch?",
                          **{"to": [{"name": "", "addr": "fragen@mitmachverein.de"}]}),
                   meine_adressen=frozenset({"ich@meine-domain.de"}),
                   geschaeftsfreie_domains=frozenset({"mitmachverein.de"}))
    assert "geschaeft_an_domain_ohne_geschaeft" not in res.signals
