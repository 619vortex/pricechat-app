import { PageHeader } from "../../components/ui";
import { gummyBaked } from "../../components/nav";

/** Admin → Side Projects → GUMMY & BAKED Audit (owner, 2026-11-23: "keep GUMMY & BAKED as a separate side project ... The admin panel
 *  should simply provide access to it"). Only links: the audit has its own backend, database and sign-in, and Price Chat never calls it. */
export function GummyBakedPage() {
  const p = gummyBaked();
  return (
    <div className="page">
      <PageHeader title="GUMMY & BAKED Audit" subtitle="A separate side project: the public audit of the GUMMY/BAKED airdrop campaign." />
      <section className="card side-project">
        <p>
          The audit runs on its own: its own website, backend, database and moderator sign-in. Price Chat doesn't store or read any of
          its data; this page only opens it.
        </p>
        <div className="side-project-links">
          <a className="btn btn-primary" href={p.siteUrl} target="_blank" rel="noopener noreferrer">Open the audit site ↗</a>
          {p.adminUrl && <a className="btn" href={p.adminUrl} target="_blank" rel="noopener noreferrer">Open audit moderation ↗</a>}
        </div>
        <dl className="side-project-facts">
          <dt>Site</dt><dd><code>{p.siteUrl}</code></dd>
          <dt>Moderation</dt><dd>{p.adminUrl ? <code>{p.adminUrl}</code> : "Not set (VITE_GUMMY_AUDIT_ADMIN_URL)"}</dd>
          <dt>Status</dt>
          <dd>{p.online ? "Online" : "Not online yet: it runs on your PC only. Start it from C:\AI\banter-airdrop-record with node backend/dev/server.mjs, then use the buttons above."}</dd>
        </dl>
      </section>
    </div>
  );
}
