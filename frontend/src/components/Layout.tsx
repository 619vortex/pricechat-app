import { Suspense, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { adminHome, type TodoItem } from "../api/home";
import { useApi } from "../api/useApi";
import { useAdmin } from "./AdminGate";
import { InArea } from "./ui";
import { HomeData } from "./hub";
import { AREAS, SIDE_PROJECTS, isSideProjectPath, isTerminalPath, locate, searchJump, type NavIconKey } from "./nav";

/** Six section icons, inline stroke SVG (no emoji). */
export function NavIcon({ name, size = 18 }: { name: NavIconKey; size?: number }) {
  const p = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "overview":
      return <svg {...p}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>;
    case "content":
      return <svg {...p}><path d="M4 20h4l10-10-4-4L4 16v4z" /><path d="M13 7l4 4" /></svg>;
    case "publishing":
      return <svg {...p}><path d="M3 11l18-7-7 18-2-8-9-3z" /></svg>;
    case "terminal":
      return <svg {...p}><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" /></svg>;
    case "community":
      return <svg {...p}><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.8-3.5 3.4-5.5 6.5-5.5s5.7 2 6.5 5.5" /><circle cx="17.5" cy="9" r="2.5" /><path d="M17 14.5c2.3.3 3.9 2 4.5 4.5" /></svg>;
    case "system":
      return <svg {...p}><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" /></svg>;
  }
}

/** "Jump to…": any section or page by name or description. Ctrl+K (Cmd+K) focuses it; arrows move, Enter opens, Escape closes. */
export function JumpBox() {
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const results = searchJump(q);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        input.current?.focus();
        input.current?.select();
      }
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, []);
  useEffect(() => setSel(0), [q]);

  const go = (to: string) => {
    setQ("");
    input.current?.blur();
    navigate(to);
  };
  const onKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, Math.max(results.length - 1, 0))); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === "Enter" && results[sel]) { e.preventDefault(); go(results[sel].to); }
    else if (e.key === "Escape") { e.preventDefault(); setQ(""); input.current?.blur(); }
  };

  return (
    <div className="jump">
      <label className="jump-field">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
        <input
          ref={input}
          type="search"
          aria-label="Jump to a page"
          placeholder="Jump to…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKey}
          role="combobox"
          aria-expanded={results.length > 0}
          aria-controls="jump-results"
          aria-activedescendant={results.length ? `jump-opt-${sel}` : undefined}
          autoComplete="off"
        />
        <kbd className="jump-kbd" aria-hidden="true">Ctrl K</kbd>
      </label>
      {q && (
        <ul className="jump-results" id="jump-results" role="listbox" aria-label="Pages">
          {results.length === 0 && <li className="jump-empty">No page matches “{q}”</li>}
          {results.map((r, i) => (
            <li key={`${r.to}|${r.label}`} id={`jump-opt-${i}`} role="option" aria-selected={i === sel}>
              <button type="button" className={`jump-opt${i === sel ? " active" : ""}`} onMouseEnter={() => setSel(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => go(r.to)}>
                <span className="jump-label">{r.label}</span>
                <span className="jump-section">{r.section}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Everything waiting for the owner, counted from the home to-do list (already polled), with a link to each queue. */
export function PendingButton({ todo }: { todo: TodoItem[] }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const { pathname } = useLocation();
  const waiting = todo.filter((t) => t.count > 0);
  const total = waiting.reduce((n, t) => n + t.count, 0);

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const out = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", out);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", out); document.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div className="pending" ref={box}>
      <button type="button" className={`pending-btn${total ? "" : " pending-none"}`} aria-expanded={open} aria-haspopup="true" onClick={() => setOpen((v) => !v)}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true"><path d="M5 12l4 4 10-10" /></svg>
        <span className="pending-label">Pending</span>
        <span className="pending-count" aria-label={`${total} waiting`}>{total}</span>
      </button>
      {open && (
        <div className="pending-pop" role="dialog" aria-label="Waiting for you">
          {waiting.length === 0 ? <p className="pending-clear">Nothing is waiting for you.</p> : (
            <ul>
              {waiting.map((t) => (
                <li key={t.key}><Link to={t.to}><b>{t.count}</b><span>{t.label}</span></Link></li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/** The admin shell: a short sidebar (six sections, search, what's waiting), and the section's pages as tabs above the page, each with
 *  one line saying what it's for. On phones the sidebar is a menu, and a bottom bar holds the six sections, except on Price Chat
 *  Terminal, whose frame stays exactly as it was (owner, 2026-11-22). */
export function Layout() {
  const admin = useAdmin();
  const { pathname } = useLocation();
  const home = useApi(adminHome, 30000);
  const [menu, setMenu] = useState(false);
  const { area, page } = locate(pathname);
  const terminal = isTerminalPath(pathname);
  const side = isSideProjectPath(pathname);  // a side project's page belongs to no section: none is highlighted
  // owner, 2026-11-23: Side Projects only for a signed-in admin (or the local dashboard, where no login is asked)
  const signedIn = Boolean(admin.session && (admin.session.authenticated || !admin.session.required));
  const todo = home.data?.todo ?? [];
  const waiting = (keys: string[] = []) => todo.filter((t) => keys.includes(t.key)).reduce((n, t) => n + t.count, 0);
  const s = home.data?.status;

  useEffect(() => setMenu(false), [pathname]);

  return (
    <div className={`shell${menu ? " menu-open" : ""}${terminal ? "" : " has-bottombar"}`}>
      <header className="topbar">
        <button className="topbar-menu" onClick={() => setMenu((v) => !v)} aria-expanded={menu} aria-label="Menu">☰</button>
        <Link to="/" className="brand"><span className="brand-mark">P</span><span className="brand-name">Price Chat admin</span></Link>
      </header>
      <aside className="sidebar">
        <Link to="/" className="brand"><span className="brand-mark">P</span><span className="brand-name">Price Chat admin</span></Link>
        <div className="sidebar-tools">
          <JumpBox />
          <PendingButton todo={todo} />
        </div>
        <nav aria-label="Admin sections">
          {AREAS.map((a) => {
            const n = waiting(a.todo);
            return (
              <NavLink key={a.key} to={a.pages[0].to} end className={() => `nav-link${a.key === area.key && !side ? " active" : ""}`} aria-current={a.key === area.key && !side ? "page" : undefined}>
                <span className="nav-icon"><NavIcon name={a.icon} /></span>
                <span className="nav-label">{a.label}</span>
                {n > 0 && <span className="nav-count" aria-label={`${n} waiting`}>{n}</span>}
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          {signedIn && <nav className="side-projects" aria-label="Side projects">
            <span className="side-projects-head">Side Projects</span>
            {SIDE_PROJECTS.map((p) => (
              <NavLink key={p.to} to={p.to} className={({ isActive }) => `side-project-link${isActive ? " active" : ""}`} title={p.about}>{p.label}</NavLink>
            ))}
          </nav>}
          {s?.kill_switch && <Link to="/settings" className="kill-indicator">Emergency stop is ON</Link>}
          {admin.session?.required && (
            <button className="signout" onClick={() => void admin.signOut()}>Sign out{admin.session.username ? ` (${admin.session.username})` : ""}</button>
          )}
        </div>
      </aside>
      <button className="scrim" aria-label="Close menu" tabIndex={-1} onClick={() => setMenu(false)} />
      <main className="main">
        {area.pages.length > 1 && !area.hub && (
          <div className="area-head">
            <span className="area-title">{area.label}</span>
            <nav className="subnav" aria-label={area.label}>
              {area.pages.map((p) => (
                <NavLink key={p.to} to={p.to} end className={() => `subnav-link${p.to === page.to ? " active" : ""}`}>{p.label}</NavLink>
              ))}
            </nav>
            <p className="area-about">{page.about}</p>
          </div>
        )}
        {area.hub && page.kind === "tool" && (
          <Link to={area.pages[0].to} className="hub-back">← {area.label}</Link>
        )}
        <HomeData.Provider value={home.data ?? null}>
          <InArea.Provider value={!area.hub && area.pages.length > 1 && (pathname.replace(/\/+$/, "") || "/") === page.to}><Suspense fallback={<div className="page-loading" aria-busy="true" />}><Outlet /></Suspense></InArea.Provider>
        </HomeData.Provider>
      </main>
      {!terminal && (
        <nav className="bottombar" aria-label="Admin sections (phone)">
          {AREAS.map((a) => {
            const n = waiting(a.todo);
            return (
              <Link key={a.key} to={a.pages[0].to} className={`bottombar-link${a.key === area.key && !side ? " active" : ""}`} aria-current={a.key === area.key && !side ? "page" : undefined}>
                <span className="bottombar-icon"><NavIcon name={a.icon} size={20} />{n > 0 && <span className="bottombar-dot" aria-label={`${n} waiting`} />}</span>
                <span className="bottombar-label">{a.short}</span>
              </Link>
            );
          })}
        </nav>
      )}
    </div>
  );
}
