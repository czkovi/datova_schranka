"""
Stažení přijatých a odeslaných zpráv z Datové schránky jako ZFO soubory.
Stahuje pouze zprávy, které ještě nebyly staženy (sleduje v downloaded.json).
Loguje do konzole i do datova_schranka.log.

SOAP endpointy ISDS:
  /DS/dx  – dm_info (seznamy zpráv)
  /DS/dz  – dm_operations (stahování zpráv)

Instalace:
    pip install requests lxml
"""

import os
import sys
import json
import base64
import logging
from datetime import datetime, timedelta

try:
    import requests
    from lxml import etree
except ImportError:
    print("Chybi zavislosti. Nainstaluj je prikazem:")
    print("  pip install requests lxml")
    sys.exit(1)


# ── Konfigurace ──────────────────────────────────────────────────────────────

USERNAME = os.environ.get("ISDS_USERNAME", "")
PASSWORD = os.environ.get("ISDS_PASSWORD", "")

USE_TEST_ENV = False

OUTPUT_DIR_RECEIVED = "prijate_zpravy"
OUTPUT_DIR_SENT = "odeslane_zpravy"

DOWNLOADED_DB = "downloaded.json"
LOG_FILE = "datova_schranka.log"

DAYS_BACK = 90

# ── Logging ──────────────────────────────────────────────────────────────────

log = logging.getLogger("isds")
log.setLevel(logging.DEBUG)

fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# Konzole - pouzijeme sys.stdout s explicitnim utf-8 a errors=replace
console_stream = open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
console = logging.StreamHandler(console_stream)
console.setLevel(logging.INFO)
console.setFormatter(fmt)
log.addHandler(console)

# Soubor - DEBUG a vys
file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(fmt)
log.addHandler(file_handler)

# ── SOAP ─────────────────────────────────────────────────────────────────────

PROD_HOST = "ws1.mojedatovaschranka.cz"
TEST_HOST = "ws1.czebox.cz"

NS = "http://isds.czechpoint.cz/v20"


def get_host() -> str:
    return TEST_HOST if USE_TEST_ENV else PROD_HOST


def soap_request(endpoint_path: str, soap_body: str):
    host = get_host()
    url = f"https://{host}{endpoint_path}"

    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope
    xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:isds="{NS}">
  <SOAP-ENV:Body>
    {soap_body}
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": '""',
    }

    log.debug("SOAP request: %s %s", url, soap_body.strip()[:120])

    resp = requests.post(
        url,
        data=envelope.encode("utf-8"),
        headers=headers,
        auth=(USERNAME, PASSWORD),
        timeout=120,
    )

    log.debug("SOAP response: HTTP %d, %d bytes", resp.status_code, len(resp.content))

    if resp.status_code != 200:
        log.error("HTTP %d z %s: %s", resp.status_code, url, resp.text[:500])
        raise Exception(f"HTTP {resp.status_code}")

    return etree.fromstring(resp.content)


def ns(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def sanitize_filename(name: str) -> str:
    forbidden = '<>:"/\\|?*\n\r'
    for ch in forbidden:
        name = name.replace(ch, "_")
    return name.strip()[:100]


def get_status(root):
    code = ""
    msg = ""
    for tag in ["dbStatusCode", "dmStatusCode"]:
        for el in root.iter(ns(tag)):
            code = el.text or ""
            break
        if code:
            break
    for tag in ["dbStatusMessage", "dmStatusMessage"]:
        for el in root.iter(ns(tag)):
            msg = el.text or ""
            break
        if msg:
            break
    return code, msg


# ── Sledovani stazenych zprav ────────────────────────────────────────────────

def load_downloaded() -> dict:
    if os.path.exists(DOWNLOADED_DB):
        try:
            with open(DOWNLOADED_DB, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "received" not in data:
                data["received"] = []
            if "sent" not in data:
                data["sent"] = []
            return data
        except (json.JSONDecodeError, KeyError):
            log.warning("Poskozeny %s, vytvarim novy.", DOWNLOADED_DB)
    return {"received": [], "sent": []}


def save_downloaded(db: dict):
    with open(DOWNLOADED_DB, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


# ── ISDS operace ─────────────────────────────────────────────────────────────

def list_messages(direction: str, from_date: datetime, to_date: datetime) -> list:
    from_str = from_date.strftime("%Y-%m-%dT%H:%M:%S.000+01:00")
    to_str = to_date.strftime("%Y-%m-%dT%H:%M:%S.000+01:00")

    if direction == "received":
        operation = "GetListOfReceivedMessages"
    else:
        operation = "GetListOfSentMessages"

    body = f"""<isds:{operation}>
      <isds:dmFromTime>{from_str}</isds:dmFromTime>
      <isds:dmToTime>{to_str}</isds:dmToTime>
      <isds:dmStatusFilter>-1</isds:dmStatusFilter>
      <isds:dmOffset>1</isds:dmOffset>
      <isds:dmLimit>10000</isds:dmLimit>
    </isds:{operation}>"""

    try:
        root = soap_request("/DS/dx", body)
    except Exception as e:
        log.error("Chyba pri vypisu %s zprav: %s", direction, e)
        return []

    code, msg = get_status(root)
    if code and code != "0000":
        log.error("ISDS chyba pri vypisu %s: %s - %s", direction, code, msg)
        return []

    messages = []
    for record in root.iter(ns("dmRecord")):
        dm_id = ""
        annotation = ""
        delivery_time = ""

        el = record.find(ns("dmID"))
        if el is not None and el.text:
            dm_id = el.text

        el = record.find(ns("dmAnnotation"))
        if el is not None and el.text:
            annotation = el.text

        el = record.find(ns("dmDeliveryTime"))
        if el is not None and el.text:
            delivery_time = el.text
        if not delivery_time:
            el = record.find(ns("dmAcceptanceTime"))
            if el is not None and el.text:
                delivery_time = el.text

        if dm_id:
            messages.append((dm_id, annotation, delivery_time))

    log.debug("Vypis %s: %d zprav nalezeno", direction, len(messages))
    return messages


def download_zfo(msg_id: str, annotation: str, delivery_time: str,
                 output_dir: str, direction: str) -> bool:
    if direction == "received":
        operation = "SignedMessageDownload"
    else:
        operation = "SignedSentMessageDownload"

    body = f"""<isds:{operation}>
      <isds:dmID>{msg_id}</isds:dmID>
    </isds:{operation}>"""

    try:
        root = soap_request("/DS/dz", body)
    except Exception as e:
        log.error("Stahovani %s selhalo: %s", msg_id, e)
        return False

    code, msg = get_status(root)
    if code and code != "0000":
        log.error("Stahovani %s: %s - %s", msg_id, code, msg)
        return False

    # Hledej ZFO data
    zfo_data = None

    for el in root.iter(ns("dmSignature")):
        if el.text and el.text.strip():
            try:
                zfo_data = base64.b64decode(el.text.strip())
                break
            except Exception:
                pass

    if not zfo_data:
        for el in root.iter():
            if el.text and len(el.text.strip()) > 500:
                try:
                    decoded = base64.b64decode(el.text.strip())
                    if len(decoded) > 100:
                        zfo_data = decoded
                        break
                except Exception:
                    continue

    if not zfo_data:
        log.error("Stahovani %s: ZFO data nenalezena v odpovedi", msg_id)
        return False

    date_prefix = delivery_time[:19].replace(":", "-").replace("T", "_") if delivery_time else "00000000"
    safe_annotation = sanitize_filename(annotation) if annotation else "zprava"
    filename = f"{date_prefix}_{msg_id}_{safe_annotation}.zfo"

    filepath = os.path.join(output_dir, filename)
    with open(filepath, "wb") as f:
        f.write(zfo_data)

    size_kb = len(zfo_data) / 1024
    log.info("  [OK] %s | %s | %.1f kB", msg_id, annotation[:50], size_kb)
    log.debug("Ulozeno: %s", filepath)
    return True


def process_messages(direction: str, from_date: datetime, to_date: datetime, db: dict):
    label = "Prijate" if direction == "received" else "Odeslane"
    output_dir = OUTPUT_DIR_RECEIVED if direction == "received" else OUTPUT_DIR_SENT

    log.info("--- %s zpravy ---", label)
    all_messages = list_messages(direction, from_date, to_date)
    already = set(db[direction])

    new_messages = [(mid, ann, dt) for mid, ann, dt in all_messages if mid not in already]
    skipped = len(all_messages) - len(new_messages)

    log.info("Nalezeno %d zprav, %d jiz stazeno, %d novych.",
             len(all_messages), skipped, len(new_messages))

    downloaded_count = 0
    failed_count = 0
    for msg_id, annotation, delivery_time in new_messages:
        if download_zfo(msg_id, annotation, delivery_time, output_dir, direction):
            db[direction].append(msg_id)
            save_downloaded(db)
            downloaded_count += 1
        else:
            failed_count += 1

    log.info("Stazeno: %d novych zprav, %d selhalo.", downloaded_count, failed_count)


def main():
    log.info("=" * 60)
    log.info("Spusteni ISDS stahovani - %s", datetime.now().strftime("%d.%m.%Y %H:%M:%S"))

    if not USERNAME or not PASSWORD:
        log.error("Chybi prihlasoaci udaje. Nastav je:")
        log.error("  set ISDS_USERNAME=tvoje_id")
        log.error("  set ISDS_PASSWORD=tvoje_heslo")
        sys.exit(1)

    env_label = "TESTOVACI (czebox)" if USE_TEST_ENV else "PRODUKCNI"
    log.info("Prostredi: %s (%s)", env_label, get_host())

    os.makedirs(OUTPUT_DIR_RECEIVED, exist_ok=True)
    os.makedirs(OUTPUT_DIR_SENT, exist_ok=True)

    to_date = datetime.now()
    from_date = to_date - timedelta(days=DAYS_BACK)
    log.info("Obdobi: %s - %s", from_date.strftime("%d.%m.%Y"), to_date.strftime("%d.%m.%Y"))

    db = load_downloaded()
    total_tracked = len(db["received"]) + len(db["sent"])
    log.info("Databaze: %d jiz stazenych zprav (%s)", total_tracked, DOWNLOADED_DB)

    process_messages("received", from_date, to_date, db)
    process_messages("sent", from_date, to_date, db)

    total_now = len(db["received"]) + len(db["sent"])
    log.info("--- Hotovo ---")
    log.info("Celkem sledovano: %d zprav", total_now)
    log.info("Prijate: %s", os.path.abspath(OUTPUT_DIR_RECEIVED))
    log.info("Odeslane: %s", os.path.abspath(OUTPUT_DIR_SENT))
    log.info("Log: %s", os.path.abspath(LOG_FILE))


if __name__ == "__main__":
    main()