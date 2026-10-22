import { Suspense, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { adminHome } from "../api/home";
import { useApi } from "../api/useApi";
import { useAdmin } from "./AdminGate";
import { InArea } from "./ui";
import { AREAS, locate } from "./nav";

/** The admin shell: a short sidebar of areas (with a count of what's waiting in each), and the area's pages as tabs above
 *  the page, each with one line saying what it's for. On phones the sidebar is a menu. */
export function Layout() {
  const admin = useAdmin();
  const { pathname } = useLocation();
  const home = useApi(adminHome, 30000);
  const [menu, setMenu] = useState(false);
  const { area, page } = locate(pathname);
  const waiting = (keys: string[] = []) => (home.data?.todo ?? []).filter((t) => keys.includes(t.key)).reduce((n, t) => n + t.count, 0);
  const s = home.data?.status;

  useEffect(() => setMenu(false), [pathname]);

  return (
    <div className={`shell${menu ? " menu-open" : ""}`}>
      <header className="topbar">
        <button className="topbar-menu" onClick={() => setMenu((v) => !v)} aria-expanded={menu} aria-label="Menu">☰</button>
        <Link to="/" className="brand"><span className="brand-mark">P</span><span className="brand-name">Price Chat admin</span></Link>
      </header>
      <aside className="sidebar">
        <Link to="/" className="brand"><span className="brand-mark">P</span><span className="brand-name">Price Chat admin</span></Link>
        <nav aria-label="Admin areas">
          {AREAS.map((a) => {
            const n = waiting(a.todo);
            return (
              <NavLink key={a.key} to={a.pages[0].to} end className={() => `nav-link${a.key === area.key ? " active" : ""}`}>
                <span className="nav-icon" aria-hidden>{a.icon}</span>
                <span className="nav-label">{a.label}</span>
                {n > 0 && <span className="nav-count" aria-label={`${n} waiting`}>{n}</span>}
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-foot">
          {s?.kill_switch && <Link to="/settings" className="kill-indicator">Emergency stop is ON</Link>}
          {admin.session?.required && (
            <button className="signout" onClick={() => void admin.signOut()}>Sign out{admin.session.username ? ` (${admin.session.username})` : ""}</button>
          )}
        </div>
      </aside>
      <button className="scrim" aria-label="Close menu" tabIndex={-1} onClick={() => setMenu(false)} />
      <main className="main">
        {area.pages.length > 1 && (
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
        <InArea.Provider value={area.pages.length > 1 && (pathname.replace(/\/+$/, "") || "/") === page.to}><Suspense fallback={<div className="page-loading" aria-busy="true" />}><Outlet /></Suspense></InArea.Provider>
      </main>
    </div>
  );
}
