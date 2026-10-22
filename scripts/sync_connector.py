#!/usr/bin/env python3
"""Keep the claude.ai connector in sync with the current public tunnel URL.

A Cloudflare *quick* tunnel changes its hostname whenever it restarts, which
invalidates the connector URL that was registered in claude.ai.  This script
drives the Minis built-in browser (which holds the claude.ai session) to
rewrite the connector: it removes the stale entry and re-adds it with the
current URL, the Authorization header and "always allow" tool permissions.

Usage:
    python3 scripts/sync_connector.py [--name "미니스 브리지"] [--dry-run]
"""
import argparse
import json
import re
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("MINIS_BRIDGE_STATE_DIR", "/var/minis/shared/claude-bridge")
BROWSER = os.environ.get("MINIS_BROWSER_CLI", "minis-browser-use")


def current_url():
    """Prefer the stable ngrok domain; fall back to the quick tunnel."""
    for name in ("ngrok-url.txt", "public-url.txt"):
        try:
            with open(os.path.join(STATE_DIR, name)) as f:
                url = f.read().strip()
            if url:
                return url
        except OSError:
            continue
    return ""


def token():
    try:
        with open(os.path.join(STATE_DIR, "token")) as f:
            return f.read().strip()
    except OSError:
        return ""


def browser(action, **kwargs):
    payload = {"action": action}
    payload.update(kwargs)
    try:
        out = subprocess.run([BROWSER, "--json", json.dumps(payload)],
                             capture_output=True, text=True, timeout=180)
    except Exception as e:                       # pragma: no cover
        return {"_error": str(e)}
    raw = out.stdout.strip()
    try:
        return json.loads(raw)
    except ValueError:
        return {"_raw": raw[:500]}


def js(script):
    """Run JS in the in-app WebView and return the *result string*.

    minis-browser-use answers with
        {"action": "execute_js", "data": {"result": "...", "page_url": "..."}}
    so the value has to be unwrapped - otherwise every comparison sees the
    whole JSON envelope and nothing ever matches.
    """
    res = browser("execute_js", script=script)
    if isinstance(res, dict):
        data = res.get("data")
        if isinstance(data, dict):
            for key in ("text", "result", "value"):
                val = data.get(key)
                if isinstance(val, str):
                    # the tool appends "\n  tab_id: N" to the result
                    return re.sub(r"\s*tab_id:\s*\d+\s*$", "", val).strip()
        if isinstance(res.get("result"), str):
            return res["result"]
    return json.dumps(res, ensure_ascii=False)


def wait(seconds):
    time.sleep(seconds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=os.environ.get("MINIS_CONNECTOR_NAME", "미니스 브리지"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base = current_url()
    tok = token()
    if not base or not tok:
        print("no public url / token in %s" % STATE_DIR)
        return 2
    mcp_url = "%s/mcp?token=%s" % (base, tok)
    print("current public url : %s" % base)
    if args.dry_run:
        return 0

    # 1. open the connector directory and switch to "my items"
    browser("navigate", url="https://claude.ai/customize/connectors")
    wait(4)
    js("""
    const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let n; while(n=w.nextNode()){ if((n.textContent||'').trim()==='내 항목'){ n.parentElement.click(); return 'clicked'; } }
    return 'not found';
    """)
    wait(3)

    # 2. open the connector and read its URL
    out = js("""
    const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let n; while(n=w.nextNode()){
      if((n.textContent||'').trim()===NAME){
        n.parentElement.click();
        return 'opened';
      }
    }
    return 'not found';
    """.replace("NAME", json.dumps(args.name)))
    wait(3)
    detail = js("""
    const m=document.body.innerText.match(/https:\\/\\/[a-z0-9-]+\\.trycloudflare\\.com[^\\s]*/);
    return m ? m[0] : 'none';
    """)
    print("connector url now : %s" % detail[:120])
    if mcp_url in detail:
        print("already in sync - nothing to do")
        return 0

    # 3. delete the stale connector
    js("""
    const b=[...document.querySelectorAll('button')].find(x=>(x.getAttribute('aria-label')||'').includes('더 많은 옵션'));
    if(b){ b.click(); return 'menu'; } return 'no menu';
    """)
    wait(1)
    js("""
    const d=[...document.querySelectorAll('[role="menuitem"],li,button')].find(x=>x.offsetParent&&(x.innerText||'').trim()==='삭제');
    if(d){ d.click(); return 'delete clicked'; } return 'no delete';
    """)
    wait(2)
    js("""
    const c=[...document.querySelectorAll('button')].filter(b=>b.offsetParent&&(b.innerText||'').trim()==='삭제');
    if(c.length){ c[c.length-1].click(); return 'confirmed'; } return 'no confirm';
    """)
    wait(3)

    # 4. add the new one
    js("""
    const a=[...document.querySelectorAll('button')].filter(b=>b.offsetParent&&/추가/.test((b.innerText||'').trim())&&(b.innerText||'').trim().length<8);
    if(a.length){ a[0].click(); return 'add menu'; } return 'no add';
    """)
    wait(1)
    js("""
    const it=[...document.querySelectorAll('[role="menuitem"],li,button')].find(x=>x.offsetParent&&(x.innerText||'').includes('커스텀 커넥터 추가'));
    if(it){ it.click(); return 'custom'; } return 'no custom';
    """)
    wait(3)
    js("""
    const dlg=[...document.querySelectorAll('[role="dialog"]')].find(d=>d.querySelector('input[type="url"]'));
    if(!dlg) return 'no dialog';
    const setV=(el,v)=>{const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value');d.set.call(el,v);
      el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));};
    setV(dlg.querySelector('input[placeholder="이름"]'), NAME);
    setV(dlg.querySelector('input[type="url"]'), URL);
    const na=dlg.querySelector('input[type="radio"][value="no_auth"]');
    if(na) na.click();
    return 'filled';
    """.replace("NAME", json.dumps(args.name)).replace("URL", json.dumps(mcp_url)))
    wait(2)
    js("""
    const dlg=[...document.querySelectorAll('[role="dialog"]')].find(d=>(d.innerText||'').includes('커스텀 커넥터'));
    if(!dlg) return 'no dialog';
    const c=[...dlg.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='계속'||(b.innerText||'').trim()==='그래도 계속');
    if(c){ c.click(); return 'continue'; } return 'no continue';
    """)
    wait(3)
    js("""
    const dlg=[...document.querySelectorAll('[role="dialog"]')].find(d=>(d.innerText||'').includes('2단계')||(d.innerText||'').includes('커스텀 커넥터'));
    if(!dlg) return 'no dialog';
    const add=[...dlg.querySelectorAll('button')].find(b=>(b.innerText||'').includes('헤더 추가'));
    if(add) add.click();
    return 'header form';
    """)
    wait(1)
    js("""
    const dlg=[...document.querySelectorAll('[role="dialog"]')].find(d=>(d.innerText||'').includes('2단계'));
    if(!dlg) return 'no dialog';
    const setV=(el,v)=>{const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value');d.set.call(el,v);
      el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));};
    const hn=dlg.querySelector('input[placeholder="헤더 이름"]');
    const hv=dlg.querySelector('input[placeholder="값"]');
    if(hn&&hv){ setV(hn,'Authorization'); setV(hv, HEADER); return 'header filled'; }
    return 'no header inputs';
    """.replace("HEADER", json.dumps("Bearer " + tok)))
    wait(1)
    js("""
    const dlg=[...document.querySelectorAll('[role="dialog"]')].find(d=>(d.innerText||'').includes('2단계'));
    if(!dlg) return 'no dialog';
    const a=[...dlg.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='추가');
    if(a){ a.click(); return 'added'; } return 'no add';
    """)
    wait(4)

    # 5. tool permissions -> always allow
    js("""
    const b=[...document.querySelectorAll('button')].filter(x=>x.offsetParent&&(x.innerText||'').trim().startsWith('승인 필요'));
    if(b.length){ b[0].click(); return 'perm menu'; } return 'no perm';
    """)
    wait(1)
    js("""
    const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let n; while(n=w.nextNode()){ if((n.textContent||'').trim()==='항상 허용'){ n.parentElement.click(); return 'always allow'; } }
    return 'not found';
    """)
    wait(2)
    # verify: open the connector again and read the URL back
    browser("navigate", url="https://claude.ai/customize/connectors")
    wait(4)
    js("""
    const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
    let n; while(n=w.nextNode()){ if((n.textContent||'').trim()==='내 항목'){ n.parentElement.click(); return 'ok'; } }
    return 'no';
    """)
    wait(3)
    js("""
    const els=[...document.querySelectorAll('*')].filter(e=>e.children.length===0&&(e.textContent||'').trim()==='미니스 브리지');
    if(els.length){ els[0].parentElement.click(); return 'opened'; }
    return 'not found';
    """)
    wait(3)
    check = js("""
    const m=document.body.innerText.match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com[^\s]*/);
    return m ? m[0] : 'none';
    """)
    print("connector url now : %s" % check[:120])
    if base in check:
        print("OK - connector points at the current tunnel")
        return 0
    print("!! connector still does not point at the current tunnel")
    return 1


if __name__ == "__main__":
    sys.exit(main())
