"""Real macOS Safari. Fixed-width frames check layout, never iPhone/iOS behavior."""

import json
import platform
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
STEP = "details.шаг__карточка"
CTA = 'a.кнопка[href^="https://t.me/Juliasydorgina"]:not([data-прямая])'
HERO_CTA = ".первый-экран__действие a"
DIALOG = "dialog#выбор-связи"
TELEGRAM = "https://t.me/Juliasydorgina"
MAX = "https://max.ru/u/f9LHodD0cOLPnRs6SbAJ4tX4WApMJQbghlBSH6Lw1UriFI82y_tS8nHHoPA"
SIZES = [
    ("desktop", 1440, 900), ("desktop", 1280, 600),
    ("desktop", 1024, 700), ("mobile-layout", 375, 548),
    ("mobile-layout", 390, 664), ("mobile-layout", 430, 750),
    ("mobile-layout", 390, 844), ("tablet-layout", 768, 900),
    ("landscape-layout", 844, 390),
]

report = {
    "started_utc": datetime.now(timezone.utc).isoformat(),
    "status": "not_checked", "platform": platform.platform(),
    "scope": "Real desktop Safari on a temporary macOS VM. Fixed-size same-origin "
             "iframes test responsive layout; they do not emulate iPhone, iOS, touch, "
             "safe areas or mobile browser chrome.",
    "browser": {}, "views": [], "checks": [], "errors": [], "warnings": [],
}


def check(view, name, passed, details=None, category="behavior"):
    report["checks"].append({"view": view, "category": category, "name": name,
                             "passed": bool(passed), "details": details})


def warning(view, name, details=None):
    report["warnings"].append({"view": view, "name": name, "details": details})


def attempt(view, name, fn):
    try:
        return fn()
    except Exception as exc:
        report["errors"].append({"view": view, "name": name,
                                 "error": f"{type(exc).__name__}: {exc}",
                                 "traceback": traceback.format_exc(limit=3)})
        return None


class SiteHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/__safari_harness__.html":
            body = (b'<!doctype html><html><head><meta charset="utf-8"><style>'
                    b'html,body{margin:0;background:#eee}iframe{display:block;'
                    b'border:0;margin:0}</style></head><body>'
                    b'<iframe id="site" title="responsive layout test"></iframe>'
                    b'</body></html>')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def log_message(self, *_args):
        pass


@contextmanager
def local_server():
    handler = partial(SiteHandler, directory=str(ROOT / "site"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def wait_ready(driver):
    WebDriverWait(driver, 15).until(
        lambda d: d.execute_script("return document.readyState === 'complete' && "
                                   "location.pathname === '/index.html' && !!document.querySelector('h1')"))
    driver.set_script_timeout(15)
    driver.execute_async_script("""
        const done = arguments[arguments.length - 1];
        Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 10000))])
          .then(() => requestAnimationFrame(() => requestAnimationFrame(done)));
    """)


def viewport(driver):
    return driver.execute_script("""
        return {width:innerWidth,height:innerHeight,devicePixelRatio:devicePixelRatio,
                scrollX:scrollX,scrollY:scrollY,outerWidth:outerWidth,outerHeight:outerHeight};
    """)


def resize_desktop(driver, width, height):
    driver.switch_to.default_content()
    driver.set_window_rect(x=0, y=0, width=width, height=height)
    for _ in range(4):
        actual = viewport(driver)
        if actual["width"] == width and actual["height"] == height:
            break
        outer = driver.get_window_rect()
        driver.set_window_rect(x=0, y=0,
                               width=outer["width"] + width - actual["width"],
                               height=outer["height"] + height - actual["height"])
        time.sleep(0.15)
    return viewport(driver)


def load_view(driver, origin, mode, width, height, view):
    driver.switch_to.default_content()
    frame = None
    if mode == "desktop":
        resize_desktop(driver, width, height)
        driver.get(origin + "/index.html")
    else:
        resize_desktop(driver, max(1024, width + 30), max(1050, height + 30))
        driver.get(origin + "/__safari_harness__.html")
        frame = driver.find_element(By.ID, "site")
        driver.execute_script("""
            const f=arguments[0]; f.style.width=arguments[1]+'px';
            f.style.height=arguments[2]+'px'; f.src=arguments[3];
        """, frame, width, height, origin + "/index.html")
        driver.switch_to.frame(frame)
    wait_ready(driver)
    actual = viewport(driver)
    entry = {"id": view, "mode": mode, "requested": {"width": width, "height": height},
             "actual": actual, "screenshots": []}
    report["views"].append(entry)
    if actual["width"] != width or actual["height"] != height:
        warning(view, "Requested viewport was not achieved; only actual size was checked", actual)
    return frame, entry


def geometry(driver, selector):
    return driver.execute_script("""
        const e=document.querySelector(arguments[0]); if(!e) return null;
        const r=e.getBoundingClientRect(),s=getComputedStyle(e);
        return {rect:{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom},
                display:s.display,visibility:s.visibility,opacity:s.opacity,
                position:s.position,zIndex:s.zIndex,fontFamily:s.fontFamily,
                fontSize:s.fontSize,lineHeight:s.lineHeight};
    """, selector)


def screenshot(driver, frame, entry, label):
    path = REPORTS / f'{entry["id"]}-{label}.png'
    if frame is None:
        driver.save_screenshot(str(path))
        entry["screenshots"].append({"file": path.name, "kind": "window"})
        return
    driver.switch_to.default_content()
    try:
        frame.screenshot(str(path))
        entry["screenshots"].append({"file": path.name, "kind": "iframe-element"})
    except Exception as exc:
        driver.save_screenshot(str(path))
        entry["screenshots"].append({"file": path.name, "kind": "outer-window-fallback"})
        warning(entry["id"], "Iframe screenshot fallback", str(exc))
    finally:
        driver.switch_to.frame(frame)


def scroll_to(driver, element):
    driver.execute_script("arguments[0].scrollIntoView({block:'center',inline:'nearest'})", element)
    time.sleep(0.35)


def overflow(driver, view, phase):
    data = driver.execute_script("""
        const w=innerWidth;
        return {viewport:w,document:document.documentElement.scrollWidth,
            body:document.body.scrollWidth,scrollY:scrollY,
            suspects:Array.from(document.querySelectorAll('body *')).map(e=>{
                const r=e.getBoundingClientRect(),s=getComputedStyle(e);
                return {tag:e.tagName,id:e.id,class:e.className.baseVal||e.className,
                        left:r.left,right:r.right,width:r.width,position:s.position,
                        overflowX:s.overflowX,display:s.display,visibility:s.visibility};
            }).filter(e=>e.width>0&&e.visibility!=='hidden'&&
                         (e.left < -1||e.right>w+1)).slice(0,35)};
    """)
    check(view, f"No horizontal overflow ({phase})",
          max(data["document"], data["body"]) <= data["viewport"] + 1,
          data, "layout")


def hero_check(driver, view):
    data = driver.execute_script("""
        const e=document.querySelector(arguments[0]); if(!e) return null;
        const r=e.getBoundingClientRect();
        const points=[[r.left+r.width/2,r.top+r.height/2],
                      [r.left+20,r.top+r.height/2],[r.right-20,r.top+r.height/2]];
        return {rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom},
          viewport:{width:innerWidth,height:innerHeight},
          inside:r.top>=0&&r.left>=0&&r.right<=innerWidth&&r.bottom<=innerHeight,
          hits:points.map(([x,y])=>{const hit=document.elementFromPoint(x,y);
            return {x,y,ok:!!hit&&(hit===e||e.contains(hit)),
                    hit:hit?hit.tagName+'.'+hit.className:null}})};
    """, HERO_CTA)
    check(view, "Hero CTA is fully visible and unobstructed before scrolling",
          data and data["inside"] and all(p["ok"] for p in data["hits"]), data, "layout")


def fresh_steps(driver, view):
    state = driver.execute_script("""
        return Array.from(document.querySelectorAll(arguments[0])).map(e=>({
          open:e.open,bodyHeight:e.querySelector('.шаг__тело').getBoundingClientRect().height,
          bodyRects:e.querySelector('.шаг__тело').getClientRects().length,
          title:e.querySelector('summary').textContent.trim()}));
    """, STEP)
    check(view, "All five steps closed on fresh load",
          len(state) == 5 and all(not x["open"] for x in state), state)


def step_interactions(driver, frame, entry, opened_snapshot):
    view = entry["id"]
    steps = driver.find_elements(By.CSS_SELECTOR, STEP)
    for index, step in enumerate(steps):
        def toggle(step=step, index=index):
            summary = step.find_element(By.CSS_SELECTOR, "summary")
            body = step.find_element(By.CSS_SELECTOR, ".шаг__тело")
            scroll_to(driver, summary)
            summary.click()
            WebDriverWait(driver, 4).until(lambda d: step.get_property("open"))
            check(view, f"Step {index + 1}: real click opens visible body",
                  body.is_displayed() and body.rect["height"] > 0,
                  geometry(driver, f'{STEP}:nth-of-type(1) .шаг__тело')
                  if index == 0 else {"rect": body.rect})
            overflow(driver, view, f"step {index + 1} open")
            if index == 0 and opened_snapshot:
                screenshot(driver, frame, entry, "first-step-open")
            scroll_to(driver, summary)
            summary.click()
            WebDriverWait(driver, 4).until(lambda d: not step.get_property("open"))
            check(view, f"Step {index + 1}: real click closes body",
                  not body.is_displayed(), {"open": step.get_property("open"), "rect": body.rect})
        attempt(view, f"Step {index + 1} interaction", toggle)


def images_check(driver, view):
    steps = driver.find_elements(By.CSS_SELECTOR, STEP)
    for step in steps:
        if not step.get_property("open"):
            summary = step.find_element(By.CSS_SELECTOR, "summary")
            scroll_to(driver, summary)
            summary.click()
    images = driver.find_elements(By.CSS_SELECTOR, "img")
    for img in images:
        scroll_to(driver, img)
        attempt(view, "Wait for image: " + (img.get_attribute("src") or "unknown"),
                lambda img=img: WebDriverWait(driver, 8).until(
                    lambda d: d.execute_script("return arguments[0].complete && arguments[0].naturalWidth>0", img)))
    data = driver.execute_script("""
        return Array.from(document.images).map(i=>({src:i.getAttribute('src'),
          currentSrc:i.currentSrc,complete:i.complete,naturalWidth:i.naturalWidth,
          naturalHeight:i.naturalHeight,loading:i.loading,rect:{width:i.width,height:i.height}}));
    """)
    check(view, "Exactly eight loaded images after scrolling and opening steps",
          len(data) == 8 and all(x["complete"] and x["naturalWidth"] > 0 for x in data), data, "assets")
    overflow(driver, view, "all steps open and images loaded")
    for step in steps:
        if step.get_property("open"):
            summary = step.find_element(By.CSS_SELECTOR, "summary")
            scroll_to(driver, summary)
            summary.click()


def faq_check(driver, view):
    faqs = driver.find_elements(By.CSS_SELECTOR, "details.вопрос")
    check(view, "Seven FAQ items present", len(faqs) == 7, {"count": len(faqs)})
    for index, faq in enumerate(faqs):
        def toggle(faq=faq, index=index):
            original = faq.get_property("open")
            summary = faq.find_element(By.CSS_SELECTOR, "summary")
            scroll_to(driver, summary)
            summary.click()
            WebDriverWait(driver, 4).until(lambda d: faq.get_property("open") != original)
            after = faq.get_property("open")
            scroll_to(driver, summary)
            summary.click()
            WebDriverWait(driver, 4).until(lambda d: faq.get_property("open") == original)
            check(view, f"FAQ {index + 1} toggles and restores via real clicks",
                  after != original, {"initialOpen": original, "clickedOpen": after})
        attempt(view, f"FAQ {index + 1} interaction", toggle)


def contacts_check(driver, view, all_ctas):
    ctas = driver.find_elements(By.CSS_SELECTOR, CTA)
    check(view, "Seven contact CTAs present", len(ctas) == 7, {"count": len(ctas)})
    dialog = driver.find_element(By.CSS_SELECTOR, DIALOG)
    links = {e.text: e.get_attribute("href")
             for e in dialog.find_elements(By.CSS_SELECTOR, "a")}
    check(view, "Contact chooser Telegram/MAX hrefs correct",
          set(links.values()) == {TELEGRAM, MAX}, links)
    targets = [cta for cta in ctas if cta.is_displayed()] if all_ctas else [
        driver.find_element(By.CSS_SELECTOR, HERO_CTA)]
    for index, cta in enumerate(targets):
        def click_cta(cta=cta, index=index):
            scroll_to(driver, cta)
            cta.click()
            WebDriverWait(driver, 5).until(lambda d: dialog.get_property("open"))
            check(view, f"CTA {index + 1} opens visible contact dialog",
                  dialog.is_displayed(), {"dialog": geometry(driver, DIALOG),
                  "url": driver.execute_script("return location.href")})
            check(view, f"CTA {index + 1} stayed on local site",
                  driver.execute_script("return location.hostname") == "127.0.0.1")
            dialog.find_element(By.CSS_SELECTOR, "button.выбор__закрыть").click()
            WebDriverWait(driver, 4).until(lambda d: not dialog.get_property("open"))
            check(view, f"CTA {index + 1} dialog closes via real button click",
                  not dialog.is_displayed())
        attempt(view, f"Contact CTA {index + 1} interaction", click_cta)
        if dialog.get_property("open"):
            dialog.find_element(By.CSS_SELECTOR, "button.выбор__закрыть").click()


def metadata_check(driver, view):
    data = driver.execute_script("""
        const canonical=document.querySelector('link[rel="canonical"]');
        const schemas=Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
          .map(e=>{try{return JSON.parse(e.textContent)}catch(err){return {parseError:String(err)}}});
        return {canonical:canonical&&canonical.href,schemas};
    """)
    def has_person(node):
        if isinstance(node, list):
            return any(has_person(item) for item in node)
        if isinstance(node, dict):
            kinds = node.get("@type", [])
            if kinds == "Person" or isinstance(kinds, list) and "Person" in kinds:
                return True
            return any(has_person(value) for value in node.values())
        return False
    check(view, "Canonical points to production URL",
          data["canonical"] == "https://sudorgina.ru/", data["canonical"], "metadata")
    check(view, "Valid Person JSON-LD present", has_person(data["schemas"]), data["schemas"], "metadata")


def view_checks(driver, origin, mode, width, height, index):
    view = f"{mode}-{width}x{height}"
    frame, entry = load_view(driver, origin, mode, width, height, view)
    entry["fonts"] = driver.execute_script("""
        return {status:document.fonts.status,faces:Array.from(document.fonts).map(f=>({
          family:f.family,weight:f.weight,status:f.status})),
          heading:getComputedStyle(document.querySelector('h1')).fontFamily};
    """)
    if entry["fonts"]["status"] != "loaded" or any(
            f["status"] == "error" for f in entry["fonts"]["faces"]):
        warning(view, "Font loading requires review", entry["fonts"])
    attempt(view, "Hero screenshot", lambda: screenshot(driver, frame, entry, "hero"))
    attempt(view, "Fresh horizontal overflow", lambda: overflow(driver, view, "fresh load"))
    if mode != "landscape-layout":
        attempt(view, "Fresh closed steps", lambda: fresh_steps(driver, view))
        attempt(view, "Hero CTA position", lambda: hero_check(driver, view))
    step_section = driver.find_element(By.ID, "экран-04")
    driver.execute_script("arguments[0].scrollIntoView({block:'start'})", step_section)
    time.sleep(0.35)
    attempt(view, "Closed steps screenshot", lambda: screenshot(driver, frame, entry, "steps"))
    if mode == "landscape-layout":
        attempt(view, "Steps horizontal overflow", lambda: overflow(driver, view, "steps"))
        return
    attempt(view, "Step interactions", lambda: step_interactions(
        driver, frame, entry, index in (0, 3)))
    attempt(view, "Image loading", lambda: images_check(driver, view))
    attempt(view, "FAQ interactions", lambda: faq_check(driver, view))
    attempt(view, "Contact chooser", lambda: contacts_check(driver, view, index == 0))
    if index == 0:
        attempt(view, "Metadata", lambda: metadata_check(driver, view))


def main():
    REPORTS.mkdir(parents=True, exist_ok=True)
    driver = None
    try:
        if not (ROOT / "site" / "index.html").is_file():
            raise FileNotFoundError("Built site/index.html is missing")
        with local_server() as origin:
            try:
                driver = webdriver.Safari()
            except Exception as exc:
                report["errors"].append({"category": "launch", "name": "Safari did not start",
                                         "error": f"{type(exc).__name__}: {exc}"})
                return 2
            driver.set_page_load_timeout(25)
            report["browser"] = {"capabilities": driver.capabilities,
                                 "userAgent": driver.execute_script("return navigator.userAgent")}
            is_safari = driver.capabilities.get("browserName", "").lower() == "safari"
            check("global", "Actual Safari browser", is_safari, report["browser"], "environment")
            if not is_safari:
                return 2
            report["status"] = "checking"
            for index, (mode, width, height) in enumerate(SIZES):
                print(f"Checking {mode} {width}x{height}", flush=True)
                attempt(f"{mode}-{width}x{height}", "View checks",
                        lambda mode=mode, width=width, height=height, index=index:
                        view_checks(driver, origin, mode, width, height, index))
            failed = [c for c in report["checks"] if not c["passed"]]
            report["status"] = "failed" if failed or report["errors"] else "passed"
            return 1 if report["status"] == "failed" else 0
    except Exception as exc:
        report["errors"].append({"category": "harness", "name": "Unexpected harness error",
                                 "error": f"{type(exc).__name__}: {exc}",
                                 "traceback": traceback.format_exc(limit=5)})
        if report["status"] != "not_checked":
            report["status"] = "failed"
        return 2
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                warning("global", "Safari cleanup", str(exc))
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report["summary"] = {"passed": sum(c["passed"] for c in report["checks"]),
                             "failed": sum(not c["passed"] for c in report["checks"]),
                             "errors": len(report["errors"]),
                             "warnings": len(report["warnings"]),
                             "views": len(report["views"])}
        (REPORTS / "safari-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": report["status"], **report["summary"]}), flush=True)


if __name__ == "__main__":
    sys.exit(main())
