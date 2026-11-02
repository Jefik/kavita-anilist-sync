(() => {
  const BASE = new URL(document.currentScript.src).pathname.replace(/\/panel\.js$/, "");
  const session = () => {
    try { return JSON.parse(localStorage.getItem("kavita-user"))?.token || null; } catch { return null; }
  };
  const api = async (path, method = "GET", body) => {
    const headers = {Authorization: `Bearer ${session()}`};
    if (method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-Requested-With"] = "kas"; }
    const response = await fetch(`${BASE}/api/${path}`, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  };
  const el = (tag, props = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  };
  const TONE = {updated: "ok", error: "bad", "dry-run": "warn", unmapped: "warn"};
  const STYLE = `
    :host{all:initial}*{box-sizing:border-box}
    .back{position:fixed;inset:0;z-index:100000;background:rgba(0,0,0,.6);display:flex;justify-content:center;align-items:flex-start;padding:32px 16px;overflow-y:auto;font:15px/1.45 system-ui,sans-serif;color:#e8eaed}
    .box{width:100%;max-width:760px;background:#14171b;border:1px solid #333941;border-radius:8px;padding:20px}
    .top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}h1{font-size:19px;margin:0}h2{font-size:15px;margin:0 0 8px}
    section{background:#1d2127;border:1px solid #333941;border-radius:8px;padding:14px;margin-top:12px}
    button{padding:7px 12px;border:1px solid #333941;border-radius:6px;background:transparent;color:inherit;font:inherit;cursor:pointer}button.primary{background:#7aa7f0;border-color:#7aa7f0;color:#10131a}button:disabled{opacity:.5;cursor:default}
    input[type=text],input[type=number]{width:100%;padding:7px 10px;border:1px solid #333941;border-radius:6px;background:#14171b;color:inherit;font:inherit}
    a{color:#7aa7f0}.hint{color:#9aa3ad;font-size:13px}.ok{color:#5ac486}.warn{color:#e0a84e}.bad{color:#ef7b7b}
    .row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}.grow{flex:1;min-width:140px}
    .check{display:flex;gap:8px;align-items:center;margin:6px 0}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.stats b{display:block;font-size:13px;color:#9aa3ad;font-weight:600}
    .item{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid #333941;flex-wrap:wrap}.item>div{flex:1;min-width:150px;overflow-wrap:anywhere}.item img{width:36px;height:52px;object-fit:cover;border-radius:3px}
    .log{overflow-wrap:anywhere;padding:6px 0;border-top:1px solid #333941;font-size:14px}`;

  let host = null;
  function open(title, build) {
    close();
    host = el("div");
    const root = host.attachShadow({mode: "open"});
    const box = el("div", {className: "box"});
    const shut = el("button", {textContent: "Close"});
    shut.onclick = close;
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", title);
    box.append(el("div", {className: "top"}, el("h1", {textContent: title}), shut));
    const back = el("div", {className: "back"}, box);
    back.addEventListener("mousedown", event => { if (event.target === back) close(); });
    root.append(el("style", {textContent: STYLE}), back);
    document.body.append(host);
    build(box);
  }
  function close() { host?.remove(); host = null; }
  document.addEventListener("keydown", event => { if (event.key === "Escape") close(); });
  const section = (title, ...children) => el("section", {}, el("h2", {textContent: title}), ...children);
  const hint = text => el("div", {className: "hint", textContent: text});
  const failure = (target, error) => { target.textContent = error.message; target.className = "bad"; };
  const cleanTitle = name => name.replace(/\s*[(（〔][^)）〕]*[)）〕]\s*/g, " ").replace(/\s*(フルカラー版|カラー版|新装版|究極版|完全版|愛蔵版)\s*$/, "").trim();

  function linkPicker(seriesId, name, done) {
    const query = el("input", {type: "text", value: cleanTitle(name)});
    query.setAttribute("aria-label", "Search AniList");
    const results = el("div");
    const go = el("button", {textContent: "Search"});
    go.onclick = async () => {
      results.textContent = "Searching…";
      try {
        const found = await api(`anilist/search?q=${encodeURIComponent(query.value)}`);
        results.replaceChildren(...found.map(media => {
          const use = el("button", {textContent: "Use"});
          use.onclick = async () => { await api(`mapping/${seriesId}`, "PUT", {media: media.id}); done(); };
          const link = el("a", {href: media.siteUrl, target: "_blank", rel: "noopener noreferrer", textContent: media.title.native || media.title.romaji});
          const parts = [];
          if (media.coverImage?.medium) parts.push(el("img", {src: media.coverImage.medium, alt: ""}));
          parts.push(el("div", {}, link, hint(`${media.title.english || media.title.romaji} · ${media.format || "?"} · ${media.volumes || "?"} volumes`)), use);
          return el("div", {className: "item"}, ...parts);
        }));
        if (!found.length) results.textContent = "No results.";
      } catch (error) { failure(results, error); }
    };
    return el("div", {}, el("div", {className: "row"}, el("div", {className: "grow"}, query), go), results);
  }

  async function settings() {
    open("AniList Sync", async box => {
      const status = el("div", {className: "stats"});
      const account = el("div");
      const libraries = el("div", {className: "hint", textContent: "Loading…"});
      const dry = el("input", {type: "checkbox"});
      const saved = el("span");
      const series = el("div");
      const filter = el("input", {type: "text", placeholder: "Filter by name"});
      filter.setAttribute("aria-label", "Filter by name");
      const unlinked = el("div", {className: "hint"});
      const activity = el("div");
      const preview = el("button", {textContent: "Preview in activity"});
      const send = el("button", {textContent: "Send to AniList"});
      const pastResult = el("span");
      let me = null, read = [];

      const stat = (title, text, tone) => el("div", {}, el("b", {textContent: title}), el("span", {textContent: text, className: tone || ""}));
      const showStatus = () => status.replaceChildren(
        stat("Kavita", me.realtime ? `Live as ${me.username}` : me.stream_error || "Connecting", me.realtime ? "ok" : "warn"),
        stat("AniList", me.anilist_user || me.anilist_error || "Not connected", me.anilist_user ? "ok" : me.anilist_error ? "bad" : "warn"),
        stat("Mode", me.dry_run ? "Dry run" : "Sending to AniList", me.dry_run ? "warn" : "ok"),
        stat("Waiting to retry", String(me.pending), me.pending ? "warn" : ""));

      function showAccount() {
        const parts = [];
        if (me.anilist_connected) {
          const off = el("button", {textContent: "Disconnect AniList"});
          off.onclick = async () => { me = await api("me", "PUT", {anilist_token: ""}); showAccount(); showStatus(); };
          parts.push(off);
        } else if (me.client_id) {
          parts.push(el("a", {href: `https://anilist.co/api/v2/oauth/authorize?client_id=${me.client_id}&response_type=token`, textContent: "Connect AniList"}));
        } else {
          parts.push(hint(me.is_admin ? "Set the AniList client ID below first." : "Ask the Kavita admin to set up AniList."));
        }
        account.replaceChildren(el("div", {className: "row"}, ...parts));
        if (me.is_admin) {
          const id = el("input", {type: "text", value: me.client_id});
          id.setAttribute("aria-label", "AniList client ID");
          const save = el("button", {textContent: "Save"});
          const result = el("span");
          save.onclick = async () => {
            try { const r = await api("admin", "PUT", {client_id: id.value}); me.client_id = r.client_id; showAccount(); }
            catch (error) { failure(result, error); }
          };
          account.append(
            el("div", {className: "row"}, el("div", {className: "grow"}, hint(`Admin: AniList client ID, with redirect URL ${location.origin}${BASE}/callback`), id), save, result));
        }
      }

      async function saveSync() {
        saved.textContent = ""; saved.className = "";
        try {
          me = await api("me", "PUT", {
            dry_run: dry.checked,
            libraries: [...libraries.querySelectorAll("input:checked")].map(box => Number(box.value)),
          });
          saved.textContent = "Saved"; saved.className = "ok"; showStatus(); loadSeries();
        } catch (error) { failure(saved, error); }
      }

      function showSeries() {
        const text = filter.value.trim().toLowerCase();
        const shown = read.filter(item => item.name.toLowerCase().includes(text)).slice(0, 30);
        series.replaceChildren(...shown.map(item => {
          const button = el("button", {textContent: "Open"});
          button.onclick = () => seriesDialog(item.id);
          return el("div", {className: "item"}, el("div", {}, el("div", {textContent: item.name}), hint(`${item.pages_read} of ${item.pages} pages read`)), button);
        }));
        if (!shown.length) series.replaceChildren(hint("No started series in the selected libraries."));
      }
      async function loadSeries() {
        try { read = await api("series"); showSeries(); } catch (error) { failure(series, error); }
      }
      filter.oninput = showSeries;

      async function refresh() {
        if (!host) return;
        const [state, log, missing] = await Promise.all([api("me"), api("activity"), api("unmapped")]);
        me = state; showStatus();
        preview.disabled = send.disabled = me.backfill_running;
        activity.replaceChildren(...log.slice(0, 40).map(row => el("div", {className: "log"},
          el("span", {className: "hint", textContent: `${new Date(row.at * 1000).toLocaleString()} `}),
          el("span", {className: TONE[row.kind] || "", textContent: `${row.kind} `}),
          el("span", {textContent: `${row.name ? row.name + ": " : ""}${row.detail}`}))));
        if (!log.length) activity.replaceChildren(hint("Nothing yet."));
        unlinked.replaceChildren(...missing.map(row => {
          const button = el("button", {textContent: "Find on AniList"});
          button.onclick = () => seriesDialog(row.series_id);
          return el("div", {className: "item"}, el("div", {textContent: row.name}), button);
        }));
        if (!missing.length) unlinked.textContent = "None.";
      }

      preview.onclick = () => api("backfill", "POST", {apply: false}).then(refresh).catch(error => failure(pastResult, error));
      send.onclick = () => {
        if (confirm("Send all past progress from the selected libraries to AniList?")) {
          api("backfill", "POST", {apply: true}).then(refresh).catch(error => failure(pastResult, error));
        }
      };
      const save = el("button", {textContent: "Save"});
      save.onclick = saveSync;

      box.append(
        section("Status", status),
        section("Your AniList account", hint("Only your Kavita login can see or change this."), account),
        section("Sync", el("label", {className: "check"}, dry, "Dry run: only record what automatic updates would send"),
          hint("Libraries to sync"), libraries, el("div", {className: "row"}, save, saved)),
        section("Past reading", hint("Only reads made after setup are sent automatically. Use this once for older progress. AniList is never lowered."),
          el("div", {className: "row"}, preview, send, pastResult)),
        section("Started series", el("div", {className: "row"}, el("div", {className: "grow"}, filter)), series),
        section("Series without an AniList link", unlinked),
        section("Activity", activity));

      try {
        me = await api("me");
        dry.checked = me.dry_run;
        showStatus(); showAccount();
        const found = await api("libraries");
        libraries.replaceChildren(...found.map(library => {
          const check = el("input", {type: "checkbox", value: library.id, checked: me.libraries.includes(library.id)});
          return el("label", {className: "check"}, check, library.name);
        }));
        await Promise.all([loadSeries(), refresh()]);
        const timer = setInterval(() => host ? refresh().catch(() => {}) : clearInterval(timer), 5000);
      } catch (error) { failure(status, error); }
    });
  }

  function seriesDialog(seriesId) {
    open("AniList Sync: series", async box => {
      const body = el("div", {textContent: "Loading…"});
      box.append(body);
      async function load(notice = "") {
        try {
          const detail = await api(`series/${seriesId}`);
          const media = detail.media;
          const entry = media?.mediaListEntry;
          const parts = [el("h2", {textContent: detail.name})];
          if (!detail.selected) parts.push(hint("This library is not selected for sync, so automatic updates skip it."));
          const summary = [detail.volumes_read ? `Finished in Kavita: up to volume ${detail.volumes_read}` : "No finished volume in Kavita yet"];
          const result = el("div", {className: notice ? "ok" : "", textContent: notice});
          if (media) {
            const title = media.title.native || media.title.romaji;
            parts.push(el("div", {}, "AniList: ", el("a", {href: media.siteUrl, target: "_blank", rel: "noopener noreferrer", textContent: title}),
              hint(`${entry ? `${entry.status}, ${entry.progressVolumes || 0} of ${media.volumes || "?"} volumes` : `Not on your list, ${media.volumes || "?"} volumes`}${detail.manual_link ? " · linked by you" : ""}`)));
          } else {
            parts.push(hint("No AniList link yet. Search below."));
          }
          parts.push(hint(summary[0]));
          if (media) {
            const run = async apply => {
              result.className = ""; result.textContent = "Working…";
              try {
                const outcome = await api(`series/${seriesId}/sync`, "POST", {apply});
                result.textContent = `${outcome.kind}: ${outcome.detail}`;
                result.className = TONE[outcome.kind] || "hint";
                if (apply) setTimeout(load, 800);
              } catch (error) { failure(result, error); }
            };
            const previewOne = el("button", {textContent: "Preview"});
            previewOne.onclick = () => run(false);
            const sendOne = el("button", {className: "primary", textContent: "Send"});
            sendOne.onclick = () => { if (confirm(`Send ${detail.name} to AniList?`)) run(true); };
            const volumes = el("input", {type: "number", min: "0", value: entry?.progressVolumes || 0});
            volumes.setAttribute("aria-label", "Volume progress on AniList");
            const setVolumes = el("button", {textContent: "Set on AniList"});
            setVolumes.onclick = async () => {
              const target = Number(volumes.value), current = entry?.progressVolumes || 0;
              if (target < current && !confirm(`Lower Volume Progress on AniList from ${current} to ${target}?`)) return;
              try {
                await api(`series/${seriesId}/volumes`, "PUT", {volumes: target});
                await load(`Volume Progress is now ${target} on AniList. It only goes up again when you finish a new volume or use Send.`);
              } catch (error) { failure(result, error); }
            };
            parts.push(el("div", {className: "row"}, previewOne, sendOne),
              el("div", {className: "row"}, hint("Volume Progress on AniList"), el("div", {style: "width:90px"}, volumes), setVolumes),
              hint("Type any number, higher or lower, then use Set on AniList. Send only moves it up to what you finished in Kavita."));
            if (detail.manual_link) {
              const unlink = el("button", {textContent: "Remove my link"});
              unlink.onclick = async () => { await api(`mapping/${seriesId}`, "DELETE"); await load(); };
              parts.push(el("div", {className: "row"}, unlink));
            }
          }
          parts.push(result, el("h2", {textContent: media ? "Use another AniList entry" : "Find on AniList", style: "margin-top:16px"}), linkPicker(seriesId, detail.name, load));
          body.replaceChildren(el("section", {}, ...parts));
        } catch (error) { failure(body, error); }
      }
      await load();
    });
  }

  const narrow = matchMedia("(max-width: 767px)");
  const FLOATING = "position:fixed;right:12px;bottom:12px;z-index:1030;width:44px;height:44px;border-radius:50%;background:#1d2127;color:#e8eaed;border:1px solid #333941;font-weight:700;padding:0";
  function placeButtons() {
    const inReader = /\/(manga|book|pdf)\/\d+/.test(location.pathname);
    let nav = document.getElementById("kas-nav");
    if (!session() || inReader) { nav?.remove(); document.getElementById("kas-series")?.remove(); return; }
    if (!nav) {
      nav = el("button", {id: "kas-nav", className: "btn btn-icon", title: "AniList Sync", textContent: "AL", style: narrow.matches ? FLOATING : "color:inherit;background:none;border:0;padding:0 8px;font-weight:700"});
      nav.setAttribute("aria-label", "AniList Sync");
      nav.onclick = settings;
    }
    const menu = document.querySelector("app-nav-header nav > div > ul.navbar-nav");
    if (menu && menu.nextElementSibling !== nav) menu.after(nav);
    const match = location.pathname.match(/\/series\/(\d+)/);
    const current = document.getElementById("kas-series");
    if (current && (!match || current.dataset.series !== match[1])) current.remove();
    if (match && !document.getElementById("kas-series")) {
      const anchor = document.getElementById("edit-btn--komf") || document.querySelector("app-series-detail .btn-actions");
      if (anchor?.parentElement) {
        const button = el("button", {className: "btn btn-actions", title: "AniList Sync", textContent: "AL", style: "font-weight:700"});
        button.setAttribute("aria-label", "AniList Sync for this series");
        button.onclick = () => seriesDialog(Number(match[1]));
        const wrap = el("div", {id: "kas-series", className: anchor.parentElement.className}, button);
        wrap.dataset.series = match[1];
        anchor.parentElement.insertAdjacentElement("afterend", wrap);
      }
    }
  }
  setInterval(placeButtons, 1000);
  placeButtons();
})();
