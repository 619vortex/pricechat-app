import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { PageHeader } from "../../components/ui";

export type SideProject = { key: string; label: string; about: string; available: boolean; site_url: string | null; admin_url: string | null };

/** Admin → Side Projects → GUMMY & BAKED Audit (owner, 2026-11-23: "keep GUMMY & BAKED as a separate side project ... The admin panel
 *  should simply provide access to it"). Only links: the audit has its own backend, database and sign-in, and Price Chat never reads
 *  its data. Its addresses come from the server, to the signed-in owner only (never in the public bundle), and only once it is
 *  deployed at a public https address: until then "Not available yet". */
export function GummyBakedPage() {
  const { data, failure } = useApi(() => request<{ projects: SideProject[] }>("/admin/side-projects"));
  const p = data?.projects.find((x) => x.key === "gummy-baked");
  return (
    <div className="page">
      <PageHeader title="GUMMY & BAKED Audit" subtitle="A separate side project: the public audit of the GUMMY/BAKED airdrop campaign." />
      <section className="card side-project">
        <p>
          The audit runs on its own: its own website, backend, database and moderator sign-in. Price Chat doesn't store or read any of
          its data; this page only opens it.
        </p>
        {failure ? <p className="banner banner-bad">Couldn't load this page. Try again in a moment.</p>
          : !p ? <p className="muted" aria-busy="true">Loading…</p>
          : p.available && p.site_url ? (
            <div className="side-project-links">
              <a className="btn btn-primary" href={p.site_url} target="_blank" rel="noopener noreferrer">Open the audit site ↗</a>
              {p.admin_url && <a className="btn" href={p.admin_url} target="_blank" rel="noopener noreferrer">Open audit moderation ↗</a>}
            </div>
          ) : (
            <p className="side-project-unavailable"><b>Not available yet.</b> It appears here once the audit is deployed.</p>
          )}
      </section>
    </div>
  );
}
