#!/usr/bin/env python3
"""Veckovis inventering av kommunens pågående detaljplaner."""
import csv, hashlib, json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE="https://www.lulea.se/samhalle--utveckling/samhallsplanering/detaljplaner/pagaende-detaljplaner.html"
MIRROR=BASE.replace("www.lulea.se","gymnasieskola.lulea.se")
ROOT=Path("data")
RX=re.compile(r"\b(?:KLF|KS|SBN|SBK|PL|DNR)[ -]?\d{4}\s*[/:-]\s*\d+\b",re.I)
STAGES=["laga kraft","antagen","antagande","granskning","samråd","under arbete"]
S=requests.Session()
S.headers.update({"User-Agent":"Lulea-statistik-detaljplaner/1.0 (public research; weekly request)","Accept-Language":"sv-SE,sv;q=0.9"})

def txt(el):
    return " ".join(el.get_text(" ",strip=True).split()) if el else ""
def fetch(url):
    r=S.get(url,timeout=35);r.raise_for_status()
    return BeautifulSoup(r.text,"html.parser")

def fetch_rendered(url):
    """Expand 'Visa fler' before extracting the official plan cards."""
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(locale="sv-SE")
        page.goto(url,wait_until="domcontentloaded",timeout=60000)
        page.locator("li.hiq-detail-planning-list__hit").first.wait_for(timeout=35000)
        for _ in range(20):
            shown=page.locator("li.hiq-detail-planning-list__hit").count()
            label=page.locator(".hiq-detail-planning-list__show-more-info").inner_text()
            numbers=re.search(r"Visar\s+(\d+)\s+av\s+(\d+)",label)
            expected=int(numbers.group(2)) if numbers else None
            if expected is not None and shown>=expected:break
            btn=page.locator("button.hiq-detail-planning-list__show-more-button")
            if not btn.count() or not btn.is_visible():break
            btn.click()
            try:
                page.wait_for_function("(old) => document.querySelectorAll('li.hiq-detail-planning-list__hit').length > old",arg=shown,timeout=15000)
            except Exception:page.wait_for_timeout(1200)
        html=page.content()
        browser.close()
    Path("diagnostics").mkdir(exist_ok=True)
    Path("diagnostics/last-rendered.html").write_text(html,encoding="utf-8")
    return BeautifulSoup(html,"html.parser")

def record(url,title="",area="",label=""):
    soup=fetch(url)
    main=soup.select_one("main") or soup.select_one("#content") or soup
    h=main.select_one("h1")
    title=txt(h) or title
    content=txt(main)
    case=RX.search(content)
    stage=""
    heading=main.find(string=re.compile("Här befinner vi oss i planprocessen",re.I))
    if heading:
        parent=heading.find_parent(["h2","h3","h4"])
        snippets=[]
        for nxt in (parent or heading.parent).next_siblings:
            if getattr(nxt,"name",None) in ("h2","h3"):break
            snippets.append(txt(nxt) if getattr(nxt,"name",None) else str(nxt).strip())
            if len(" ".join(snippets))>1400:break
        stage=" ".join(" ".join(snippets).split())[:900]
    if not stage:
        stage=label
    # Do not infer administrative phase from narrative unless the municipality explicitly states one.
    return {"id":case.group(0).replace(" ","") if case else hashlib.sha256(url.encode()).hexdigest()[:14],
            "arendenummer":case.group(0).replace(" ","") if case else "",
            "namn":title,"omrade":area,"status":label or "Ej angiven",
            "planprocess":stage,"url":url,"kalla":url}

def candidates(soup,base):
    cards=[]
    for li in soup.select("li.hiq-detail-planning-list__hit"):
        title=txt(li.select_one(".hiq-detail-planning-list__hit-title"))
        description=txt(li.select_one(".hiq-detail-planning-list__hit-preamble"))
        status=txt(li.select_one(".hiq-detail-planning-list__hit-status"))
        link=li.select_one("a.hiq-detail-planning-list__hit-link")
        if not title or not link:continue
        url=urljoin(base,link.get("href",""))
        case=RX.search(description+" "+title)
        cards.append({"id":hashlib.sha256(url.encode()).hexdigest()[:20],
                      "arendenummer":case.group(0).replace(" ","") if case else "",
                      "namn":title,"omrade":"","status":status or "Ej angiven",
                      "planprocess":description,"url":url,"kalla":base})
    return cards

def enrich_cards(cards):
    """Read each linked plan page; never infer an administrative area from a title."""
    for i,card in enumerate(cards,1):
        try:
            soup=fetch(card["url"])
            main=soup.select_one("main") or soup.select_one("#content") or soup
            body=txt(main)
            if not card["arendenummer"]:
                match=RX.search(body)
                if match:card["arendenummer"]=match.group(0).replace(" ","")
            # Only use explicitly labelled area/locality fields.
            for el in main.find_all(["dt","th","strong","b"]):
                key=txt(el).lower().strip(": ")
                if key in ("område","geografiskt område","stadsdel","plats"):
                    sibling=el.find_next_sibling(["dd","td","span"])
                    if sibling and 0<len(txt(sibling))<100:card["omrade"]=txt(sibling)
        except Exception as e:
            print(f"Varning: komplettering {card['url']}: {e}",flush=True)
        if i%10==0:print(f"Komplettering {i}/{len(cards)}",flush=True)
        time.sleep(.12)
    return cards

def collect():
    errors=[]
    for source in (BASE,MIRROR):
        try:
            soup=fetch_rendered(source)
            cards=candidates(soup,source)
            count_label=txt(soup.select_one(".hiq-detail-planning-list__show-more-info"))
            m=re.search(r"Visar\s+(\d+)\s+av\s+(\d+)",count_label)
            expected=int(m.group(2)) if m else None
            print(f"Diagnostik: {len(cards)} planposter; etikett={count_label}",flush=True)
            if expected is None:raise RuntimeError("Kan inte verifiera förväntat antal planer")
            if len(cards)!=expected:raise RuntimeError(f"Endast {len(cards)} av {expected} planer hämtade")
            if len({x["url"] for x in cards})!=len(cards):raise RuntimeError("Dubbletter i källans URL:er")
            return enrich_cards(cards),source,errors
        except Exception as e:errors.append(f"{source}: {e}")
    raise RuntimeError("Ingen tillförlitlig komplett inventering: "+"; ".join(errors[:12]))

def write_json(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def main():
    current,source,errors=collect()
    current.sort(key=lambda x:(x["omrade"],x["namn"],x["id"]))
    if len({x["id"] for x in current})!=len(current):raise RuntimeError("Dubbletter bland planidentiteter")
    prevfile=ROOT/"latest.json"
    previous=json.loads(prevfile.read_text(encoding="utf-8"))["planer"] if prevfile.exists() else []
    # Guard: large drops require manual review; do not overwrite a valid last snapshot.
    if previous and len(current)<len(previous)*.8:raise RuntimeError("Antalet planer föll kraftigt; avbryter för manuell kontroll")
    old={x["id"]:x for x in previous};new={x["id"]:x for x in current}
    events=[]
    for planid,x in new.items():
        if planid not in old:events.append({"typ":"ny","namn":x["namn"],"url":x["url"],"nytt":x})
        else:
            diff={k:{"tidigare":old[planid].get(k),"nu":x.get(k)} for k in ("namn","arendenummer","omrade","status","planprocess") if old[planid].get(k)!=x.get(k)}
            if diff:events.append({"typ":"andrad","namn":x["namn"],"url":x["url"],"andringar":diff})
    for planid,x in old.items():
        if planid not in new:events.append({"typ":"saknas_i_listan","namn":x["namn"],"url":x["url"]})
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    stamp=now[:10]
    snapshot={"hamtad_utc":now,"kallista":source,"antal":len(current),"planer":current,"varningar":errors}
    write_json(ROOT/"latest.json",snapshot)
    write_json(ROOT/"snapshots"/f"{stamp}.json",snapshot)
    changes_path=ROOT/"changes"/f"{stamp}.json"
    if changes_path.exists():
        old_changes=json.loads(changes_path.read_text(encoding="utf-8"))
        if old_changes.get("forsta_matning"):
            # Keep the first-day baseline intact on a same-day rerun.
            changes_path=ROOT/"changes"/f"{stamp}-{datetime.now(timezone.utc).strftime('%H%M%S')}.json"
    write_json(changes_path,{"datum":stamp,"forsta_matning":not bool(previous),"andringar":events})
    write_json(ROOT/"history_index.json",{"files":sorted(x.name for x in (ROOT/"changes").glob("*.json"))})
    with (ROOT/"latest.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["id","arendenummer","namn","omrade","status","planprocess","url","kalla"],delimiter=";")
        writer.writeheader();writer.writerows(current)
    summary=f"# Veckokontroll {stamp}\n\n- Antal observerade planer: **{len(current)}**\n- Förändringar: **{len(events)}**\n- Ursprung: {source}\n- Tidpunkt (UTC): {now}\n"
    if not previous:summary+="\nFörsta observationen: förändringar är nya poster i baslinjen, inte kommunala nytillkomna planer.\n"
    if errors:summary+="\n## Hämtningsvarningar\n"+"\n".join("- "+e for e in errors[:15])+"\n"
    summary+="\n## Förändringar\n"+"\n".join(f"- {e['typ']}: [{e['namn']}]({e['url']})" for e in events)+"\n"
    (ROOT/"CHANGELOG.md").write_text(summary,encoding="utf-8")
    print(f"OK {len(current)} planer; {len(events)} förändringar",flush=True)
if __name__=="__main__":main()
