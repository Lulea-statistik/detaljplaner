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
    """Render asynchronously loaded municipal listings with a real browser."""
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(locale="sv-SE")
        page.goto(url,wait_until="domcontentloaded",timeout=60000)
        try:
            page.get_by_text("Antnäs",exact=False).first.wait_for(timeout=25000)
        except Exception:
            pass
        page.wait_for_timeout(3500)
        html=page.content()
        text=page.locator("body").inner_text()
        browser.close()
    from pathlib import Path
    Path("diagnostics").mkdir(exist_ok=True)
    Path("diagnostics/last-rendered.html").write_text(html,encoding="utf-8")
    Path("diagnostics/last-rendered.txt").write_text(text,encoding="utf-8")
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
    """Read the complete listing, including plan cards that are not anchor elements."""
    container=soup.select_one("main") or soup.select_one("#content") or soup
    # Most municipality listings group entries by area and style each plan status.
    # Parse textual entries even when their title has no direct link.
    text=container.get_text("\n",strip=True)
    areas=["Antnäs","Bergnäset","Bergviken","Centrum","Dalbo","Gammelstad",
           "Hertsön","Kallax","Kronan","Lövskatan","Måttsund","Mjölkudden",
           "Notviken","Örnäset","Porsön","Rörbäck","Rutvik","Skärgården",
           "Storheden","Sunderbyn","Svartön"]
    lines=[l.strip() for l in text.splitlines() if l.strip()]
    plan_starts=re.compile(r"^(Under arbete|Samråd|Granskning|Antagande|Antagen|Laga kraft)\b",re.I)
    cards=[]
    area=""
    active=None
    for line in lines:
        marea=re.match(r"^("+"|".join(map(re.escape,areas))+r")\s*(?:\(\d+\))?$",line,re.I)
        if marea:
            if active: cards.append(active)
            active=None
            area=marea.group(1)
            continue
        m=plan_starts.match(line)
        if m:
            if active:cards.append(active)
            active={"omrade":area,"status":m.group(1),"parts":[line[m.end():].strip()]}
        elif active:
            if line.startswith(("Du kan också se alla","Kontakt","Detaljplanekontoret","Sidan uppdaterades")):
                cards.append(active);active=None
            elif len(" ".join(active["parts"]))<1100:
                active["parts"].append(line)
    if active:cards.append(active)
    return [c for c in cards if c["omrade"] and any("detaljplan" in x.casefold() or "planprogram" in x.casefold() or "KLF" in x for x in c["parts"])]

def collect():
    errors=[]
    for source in (BASE,MIRROR):
        try:
            soup=fetch_rendered(source)
            cards=candidates(soup,source)
            print(f'Diagnostik: {source} ger {len(cards)} planposter; Antnäs={"Antnäs" in soup.get_text()}',flush=True)
            if len(cards)<25:
                errors.append(f"{source}: endast {len(cards)} planposter");continue
            out=[]
            for card in cards:
                body=" ".join(card["parts"])
                case=RX.search(body)
                # No direct plan URL? Preserve listing URL and the extracted text.
                # Stable identity uses case number, otherwise area + title.
                clean=re.sub(r"\s+"," ",body).strip()
                title=re.split(r"\b(?:Ärendenummer|Detaljplanens ärendenummer|KLF\s*\d{4})\b",clean,maxsplit=1,flags=re.I)[0].strip()
                identity=case.group(0).replace(" ","") if case else hashlib.sha256((card["omrade"]+"|"+title).encode()).hexdigest()[:14]
                out.append({"id":identity,"arendenummer":case.group(0).replace(" ","") if case else "",
                            "namn":title[:450],"omrade":card["omrade"],"status":card["status"],
                            "planprocess":"","url":source,"kalla":source})
            if len(out)<25:raise RuntimeError("Ofullständig planlista")
            if len({x["id"] for x in out})!=len(out):
                raise RuntimeError("Icke-unika planidentiteter; kräver manuell kontroll")
            return out,source,errors
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
            diff={k:{"tidigare":old[planid].get(k),"nu":x.get(k)} for k in ("namn","arendenummer","status","planprocess") if old[url].get(k)!=x.get(k)}
            if diff:events.append({"typ":"andrad","namn":x["namn"],"url":x["url"],"andringar":diff})
    for planid,x in old.items():
        if planid not in new:events.append({"typ":"saknas_i_listan","namn":x["namn"],"url":x["url"]})
    now=datetime.now(timezone.utc).isoformat(timespec="seconds")
    stamp=now[:10]
    snapshot={"hamtad_utc":now,"kallista":source,"antal":len(current),"planer":current,"varningar":errors}
    write_json(ROOT/"latest.json",snapshot)
    write_json(ROOT/"snapshots"/f"{stamp}.json",snapshot)
    write_json(ROOT/"changes"/f"{stamp}.json",{"datum":stamp,"forsta_matning":not bool(previous),"andringar":events})
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
