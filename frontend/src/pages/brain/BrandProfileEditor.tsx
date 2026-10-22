import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import { BRAND_LIST_FIELDS, BRAND_TEXT_FIELDS, type BrandListField, type BrandProfile, type BrandTextField } from "../../api/types";
import { ApiErrorBanner, Card, Field, ListEditor, Loading, relativeTime } from "../../components/ui";

const TEXT_META: Record<BrandTextField, { label: string; hint?: string; rows: number }> = {
  brand_name: { label: "Brand name", rows: 1 },
  description: { label: "Brand description", hint: "What Price Chat is, in two or three sentences.", rows: 3 },
  mission: { label: "Mission", rows: 2 },
  audience: { label: "Audience", hint: "Who you are talking to.", rows: 2 },
  tone: { label: "Tone", hint: "e.g. clear, upbeat, no hype", rows: 1 },
  writing_style: { label: "Writing style", hint: "Sentence length, emoji use, formatting.", rows: 2 },
  personality: { label: "Personality", rows: 1 },
};

const LIST_META: Record<BrandListField, { label: string; hint: string }> = {
  products_services: { label: "Products / services", hint: "One per line." },
  topics_of_interest: { label: "Topics of interest", hint: "One per line." },
  topics_to_avoid: { label: "Topics to avoid", hint: "One per line." },
  terminology: { label: "Important terminology", hint: "One per line, e.g. “Pip: our owl mascot”." },
  allowed_claims: { label: "Allowed claims", hint: "Statements the agent may make. One per line." },
  restricted_claims: { label: "Restricted claims", hint: "Things the agent must never say or imply. One per line." },
  social_objectives: { label: "Social-media objectives", hint: "One per line." },
  x_voice: { label: "X voice: how Price Chat should sound", hint: "One trait per line, e.g. curious, direct, occasionally witty. Used for X replies and posts." },
  x_avoid: { label: "X voice: avoid", hint: "One per line, e.g. corporate language, fake enthusiasm, price predictions presented as facts." },
};

type Draft = Omit<BrandProfile, "updated_at">;

export function BrandProfileEditor({ onSaved }: { onSaved: () => void }) {
  const brand = useApi(api.brand);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [version, setVersion] = useState(0); // remounts list editors after a save/reset
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (brand.data && !draft) setDraft(stripTimestamps(brand.data.profile));
  }, [brand.data, draft]);

  if (!brand.data || !draft) return <Card title="Brand profile">{brand.failure ? <ApiErrorBanner error={brand.failure} /> : <Loading />}</Card>;

  const original = stripTimestamps(brand.data.profile);
  const changed = (Object.keys(draft) as (keyof Draft)[]).filter((k) => JSON.stringify(draft[k]) !== JSON.stringify(original[k]));

  async function save() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const changes = Object.fromEntries(changed.map((k) => [k, draft[k]]));
      const updated = await api.updateBrand(changes);
      brand.setData(updated);
      setDraft(stripTimestamps(updated.profile));
      setVersion((v) => v + 1);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
      onSaved();
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    setDraft(original);
    setVersion((v) => v + 1);
  }

  return (
    <Card
      title={`Brand profile · ${brand.data.completeness}% complete`}
      actions={
        <div className="btn-group">
          {saved && <span className="tone-good small">Saved</span>}
          <span className="muted small">updated {relativeTime(brand.data.profile.updated_at)}</span>
          <button className="btn btn-ghost btn-sm" disabled={!changed.length} onClick={reset}>
            Discard
          </button>
          <button className="btn btn-primary btn-sm" disabled={!changed.length || saving || !draft.brand_name.trim()} onClick={() => void save()}>
            {saving ? "Saving…" : changed.length ? `Save ${changed.length} change${changed.length > 1 ? "s" : ""}` : "Saved"}
          </button>
        </div>
      }
    >
      <p className="muted small">
        Everything here is stored in the database and compiled into the agent's system prompt. Leave a field empty rather than guessing: empty
        fields are left out of the prompt, and the agent is told not to invent facts.
      </p>
      <ApiErrorBanner error={error} />
      <div className="form-grid" key={version}>
        {BRAND_TEXT_FIELDS.map((f) => (
          <Field key={f} label={TEXT_META[f].label} hint={TEXT_META[f].hint} empty={!draft[f].trim()}>
            {TEXT_META[f].rows === 1 ? (
              <input className="input" value={draft[f]} onChange={(e) => setDraft({ ...draft, [f]: e.target.value })} />
            ) : (
              <textarea className="input textarea" rows={TEXT_META[f].rows} value={draft[f]} onChange={(e) => setDraft({ ...draft, [f]: e.target.value })} />
            )}
          </Field>
        ))}
        {BRAND_LIST_FIELDS.map((f) => (
          <Field key={f} label={LIST_META[f].label} hint={LIST_META[f].hint} empty={!draft[f].length}>
            <ListEditor value={draft[f]} onChange={(v) => setDraft({ ...draft, [f]: v })} />
          </Field>
        ))}
      </div>
    </Card>
  );
}

function stripTimestamps(profile: BrandProfile): Draft {
  const { updated_at: _ignored, ...rest } = profile;
  return rest;
}
