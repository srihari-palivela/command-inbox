/**
 * The draft configuration editor: structured forms for the parts people change most (categories, hard
 * stops, thresholds, gates) and the whole document as JSON for everything else. The server validates the
 * document on save; its `invalid_config` answer names the failing paths, which are shown on the fields.
 */
import type { DeploymentVersionDTO } from '@ci/contracts';
import { useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../../../lib/api';
import { keys } from '../../../lib/queries';
import { Button, Card, cx, Input, Tabs, TextArea } from '../../../ui';
import { LockIcon, ProblemAlert, Select, useInlineAction } from '../bits';
import {
  asConfig,
  errorsFor,
  errorsUnder,
  GATES,
  listFromText,
  numberOrRaw,
  parseConfigErrors,
  THRESHOLDS,
  type CategoryConfig,
  type ConfigError,
  type DeploymentConfig,
  type HardStopConfig,
  type NumberSpec,
} from '../model';
import s from '../admin.module.css';
import { AgentsSection } from './AgentsSection';

type Section = 'taxonomy' | 'hardStops' | 'agents' | 'thresholds' | 'gates' | 'json';
const SECTIONS: Section[] = ['taxonomy', 'hardStops', 'agents', 'thresholds', 'gates', 'json'];
const PREFIX: Record<Exclude<Section, 'json'>, string> = {
  taxonomy: 'taxonomy',
  agents: 'flow',
  hardStops: 'rules.hardStops',
  thresholds: 'thresholds',
  gates: 'gates',
};

export function ConfigEditor({
  deploymentId,
  version,
  editable,
}: {
  deploymentId: string;
  version: DeploymentVersionDTO;
  editable: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get('section') as Section | null;
  const tab: Section = tabParam && SECTIONS.includes(tabParam) ? tabParam : 'taxonomy';
  const setTab = (t: Section) =>
    setParams(
      (p) => {
        p.set('section', t);
        return p;
      },
      { replace: true },
    );

  const [config, setConfig] = useState<DeploymentConfig>(() => asConfig(version.config));
  // Bumped when the whole document is replaced (JSON apply, reset) so field-local text resets with it.
  const [rev, setRev] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<ConfigError[]>([]);

  const update = (fn: (c: DeploymentConfig) => void) => {
    setConfig((prev) => {
      const next = structuredClone(prev);
      fn(next);
      return next;
    });
    setDirty(true);
  };

  const save = useInlineAction(
    (c: DeploymentConfig) =>
      api.put<DeploymentVersionDTO>(`/v1/deployments/${deploymentId}/versions/${version.id}/config`, {
        config: c,
      }),
    {
      invalidate: [keys.deployments, keys.evals],
      success: (v) => `Draft v${v.version} saved. Earlier eval runs no longer count — run the evals again.`,
    },
  );
  const onSave = () =>
    save.mutate(config, {
      onSuccess: () => {
        setErrors([]);
        setDirty(false);
      },
      onError: (err) => {
        setErrors(
          err instanceof ApiError && err.code === 'invalid_config'
            ? parseConfigErrors(err.problem.detail)
            : [],
        );
      },
    });
  const reset = () => {
    setConfig(asConfig(version.config));
    setRev((r) => r + 1);
    setDirty(false);
    setErrors([]);
    save.reset();
  };

  const count = (sec: Section) =>
    sec === 'json'
      ? errors.filter((e) => !Object.values(PREFIX).some((p) => e.path === p || e.path.startsWith(`${p}.`)))
          .length
      : errorsUnder(errors, PREFIX[sec]).length;
  const badge = (sec: Section) => count(sec) || undefined;

  return (
    <Card
      className={s.card}
      title={editable ? `Configuration — draft v${version.version}` : `Configuration — v${version.version}`}
      meta={
        editable ? (
          dirty ? (
            'Unsaved changes'
          ) : (
            'Saved'
          )
        ) : (
          <span className={s.row} style={{ gap: 5 }}>
            <LockIcon /> Read only
          </span>
        )
      }
      actions={
        editable ? (
          <>
            <Button size="sm" variant="ghost" onClick={reset} disabled={!dirty || save.isPending}>
              Discard changes
            </Button>
            <Button size="sm" variant="dark" onClick={onSave} loading={save.isPending} disabled={!dirty}>
              Save draft
            </Button>
          </>
        ) : undefined
      }
    >
      <div className={s.stack}>
        {!editable && (
          <p className={s.muted}>
            {version.state === 'draft'
              ? 'Only people who can edit deployment drafts can change this.'
              : 'Published and rolled-out versions never change. Start a new draft to change the configuration.'}
          </p>
        )}
        {errors.length > 0 ? (
          <div className={s.alert} role="alert">
            <div className={s.alertTitle}>The server did not accept this configuration.</div>
            <ul className={s.errList}>
              {errors.map((e, i) => (
                <li key={i}>
                  {e.path && <code className={s.mono}>{e.path}</code>} {e.message}
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <ProblemAlert error={save.error} />
        )}
        <Tabs<Section>
          label="Configuration sections"
          value={tab}
          onChange={setTab}
          items={[
            { key: 'taxonomy', label: 'Categories', badge: badge('taxonomy') },
            { key: 'hardStops', label: 'Hard stops', badge: badge('hardStops') },
            { key: 'agents', label: 'Agents', badge: badge('agents') },
            { key: 'thresholds', label: 'Thresholds', badge: badge('thresholds') },
            { key: 'gates', label: 'Eval gates', badge: badge('gates') },
            { key: 'json', label: 'JSON', badge: badge('json') },
          ]}
        />
        <fieldset className={s.fieldset} disabled={!editable} key={rev}>
          <legend className="sr-only">Configuration</legend>
          {tab === 'taxonomy' && <Categories config={config} update={update} errors={errors} />}
          {tab === 'hardStops' && <HardStops config={config} update={update} errors={errors} />}
          {tab === 'agents' && <AgentsSection config={config} update={update} errors={errors} />}
          {tab === 'thresholds' && (
            <Numbers
              specs={THRESHOLDS}
              section="thresholds"
              values={config.thresholds ?? {}}
              update={update}
              errors={errors}
              intro="Confidence bars that decide the lane. The lane itself is always decided by policy code, never by a model."
            />
          )}
          {tab === 'gates' && (
            <Numbers
              specs={GATES}
              section="gates"
              values={config.gates ?? {}}
              update={update}
              errors={errors}
              intro="An eval run must meet every gate on this exact configuration before the version can take real mail."
              extra={
                <div className={s.field}>
                  <span className={s.fieldLabel}>
                    <LockIcon /> Auto lane on hard-stop mail
                  </span>
                  <Input value="0 — never" disabled aria-label="Auto lane on hard-stop mail (locked)" />
                  <span className={s.fieldHint}>
                    Locked: hard-stop mail never reaches the Auto lane. This is the approval gateway’s
                    premise.
                  </span>
                </div>
              }
            />
          )}
          {tab === 'json' && (
            <JsonEditor
              config={config}
              editable={editable}
              onApply={(c) => {
                setConfig(c);
                setRev((r) => r + 1);
                setDirty(true);
              }}
            />
          )}
        </fieldset>
      </div>
    </Card>
  );
}

// ── Fields ────────────────────────────────────────────────────────────────────
function FieldErrors({ errors, id }: { errors: ConfigError[]; id: string }) {
  if (!errors.length) return null;
  return (
    <span className={s.fieldError} id={id}>
      {errors.map((e) => e.message).join(' ')}
    </span>
  );
}

/** Text is kept locally while typing ("0." is not a number yet); the config gets a number when it parses. */
function NumberInput({
  value,
  onChange,
  spec,
  label,
  invalid,
  describedBy,
}: {
  value: number | string | undefined;
  onChange: (v: number | string) => void;
  spec?: Pick<NumberSpec, 'min' | 'max' | 'step'>;
  label?: string;
  invalid?: boolean;
  describedBy?: string;
}) {
  const [text, setText] = useState(value === undefined ? '' : String(value));
  return (
    <Input
      type="number"
      inputMode="decimal"
      value={text}
      min={spec?.min}
      max={spec?.max}
      step={spec?.step ?? 'any'}
      aria-label={label}
      aria-invalid={invalid || undefined}
      aria-describedby={describedBy}
      className={cx(invalid && s.invalid)}
      onChange={(e) => {
        setText(e.target.value);
        onChange(numberOrRaw(e.target.value));
      }}
    />
  );
}

type Update = (fn: (c: DeploymentConfig) => void) => void;

function Numbers({
  specs,
  section,
  values,
  update,
  errors,
  intro,
  extra,
}: {
  specs: NumberSpec[];
  section: 'thresholds' | 'gates';
  values: Record<string, number | string>;
  update: Update;
  errors: ConfigError[];
  intro: string;
  extra?: ReactNode;
}) {
  const sectionErrors = errorsFor(errors, section);
  return (
    <div className={s.stack}>
      <p className={s.muted}>{intro}</p>
      {sectionErrors.length > 0 && (
        <div className={s.alert} role="alert">
          {sectionErrors.map((e) => e.message).join(' ')}
        </div>
      )}
      <div className={s.fields}>
        {specs.map((sp) => {
          const path = `${section}.${sp.key}`;
          const errs = errorsFor(errors, path);
          const errId = `err-${path}`;
          return (
            <label key={sp.key} className={s.field}>
              <span className={s.fieldLabel}>{sp.label}</span>
              <NumberInput
                value={values[sp.key]}
                spec={sp}
                invalid={errs.length > 0}
                describedBy={errs.length ? errId : undefined}
                onChange={(v) =>
                  update((c) => {
                    c[section] = { ...(c[section] ?? {}), [sp.key]: v };
                  })
                }
              />
              <span className={s.fieldHint}>{sp.hint}</span>
              <FieldErrors errors={errs} id={errId} />
            </label>
          );
        })}
        {extra}
      </div>
    </div>
  );
}

const LANES = [
  { key: 'auto', label: 'Auto' },
  { key: 'draft', label: 'Draft' },
  { key: 'manual', label: 'You' },
] as const;
const SENSITIVITY = ['standard', 'restricted', 'secret'] as const;

function Categories({
  config,
  update,
  errors,
}: {
  config: DeploymentConfig;
  update: Update;
  errors: ConfigError[];
}) {
  const cats = config.taxonomy?.categories ?? [];
  const set = (i: number, patch: Partial<CategoryConfig>) =>
    update((c) => {
      c.taxonomy.categories[i] = { ...c.taxonomy.categories[i]!, ...patch };
    });
  const cellErr = (i: number, field: string) => errorsFor(errors, `taxonomy.categories.${i}.${field}`);
  const taxErrors = [
    ...errorsFor(errors, 'taxonomy'),
    ...errorsFor(errors, 'taxonomy.categories'),
    ...errorsFor(errors, 'taxonomy.fallback'),
  ];
  const text = (i: number, field: 'key' | 'name' | 'department' | 'description', label: string) => {
    const errs = cellErr(i, field);
    const id = `err-taxonomy.categories.${i}.${field}`;
    return (
      <>
        <Input
          value={cats[i]![field] ?? ''}
          aria-label={`Category ${i + 1} ${label}`}
          aria-invalid={errs.length > 0 || undefined}
          aria-describedby={errs.length ? id : undefined}
          className={cx(field === 'key' && 'mono', errs.length > 0 && s.invalid)}
          style={{ height: 30, fontSize: 12 }}
          onChange={(e) => set(i, { [field]: e.target.value })}
        />
        <FieldErrors errors={errs} id={id} />
      </>
    );
  };
  return (
    <div className={s.stack}>
      <p className={s.muted}>
        The labels System 1 chooses from. The description is what the decision engine reads; the default lane
        is the most autonomy a category can get.
      </p>
      {taxErrors.length > 0 && (
        <div className={s.alert} role="alert">
          {taxErrors.map((e) => e.message).join(' ')}
        </div>
      )}
      <div className={s.scroll}>
        <table className={s.table} aria-label="Categories">
          <thead>
            <tr>
              <th scope="col">Key</th>
              <th scope="col">Name</th>
              <th scope="col">Owning team</th>
              <th scope="col">Default lane</th>
              <th scope="col">Sensitivity</th>
              <th scope="col">Description</th>
              <th scope="col">
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {cats.map((cat, i) => (
              <tr key={i}>
                <td style={{ minWidth: 150 }}>{text(i, 'key', 'key')}</td>
                <td style={{ minWidth: 160 }}>{text(i, 'name', 'name')}</td>
                <td style={{ minWidth: 140 }}>{text(i, 'department', 'owning team')}</td>
                <td>
                  <Select
                    aria-label={`Category ${i + 1} default lane`}
                    value={cat.defaultLane ?? 'draft'}
                    onChange={(e) => set(i, { defaultLane: e.target.value as CategoryConfig['defaultLane'] })}
                  >
                    {LANES.map((l) => (
                      <option key={l.key} value={l.key}>
                        {l.label}
                      </option>
                    ))}
                  </Select>
                </td>
                <td>
                  <Select
                    aria-label={`Category ${i + 1} sensitivity`}
                    value={cat.sensitivity ?? 'standard'}
                    onChange={(e) => set(i, { sensitivity: e.target.value as CategoryConfig['sensitivity'] })}
                  >
                    {SENSITIVITY.map((x) => (
                      <option key={x} value={x}>
                        {x}
                      </option>
                    ))}
                  </Select>
                </td>
                <td style={{ minWidth: 220 }}>{text(i, 'description', 'description')}</td>
                <td>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove category ${cat.name || i + 1}`}
                    onClick={() => update((c) => c.taxonomy.categories.splice(i, 1))}
                  >
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={s.actions}>
        <Button
          size="sm"
          onClick={() =>
            update((c) =>
              c.taxonomy.categories.push({
                key: '',
                name: '',
                department: '',
                defaultLane: 'draft',
                sensitivity: 'standard',
                description: '',
                examples: [],
              }),
            )
          }
        >
          + Add category
        </Button>
        <label className={s.row} style={{ marginLeft: 'auto' }}>
          <span className={s.fieldLabel}>Mail that fits nothing goes to</span>
          <Select
            value={config.taxonomy?.fallback ?? 'other'}
            onChange={(e) =>
              update((c) => {
                c.taxonomy.fallback = e.target.value;
              })
            }
          >
            {cats.map((cat, i) => (
              <option key={i} value={cat.key}>
                {cat.name || cat.key || `Category ${i + 1}`}
              </option>
            ))}
          </Select>
        </label>
      </div>
    </div>
  );
}

function HardStops({
  config,
  update,
  errors,
}: {
  config: DeploymentConfig;
  update: Update;
  errors: ConfigError[];
}) {
  const stops = config.rules?.hardStops ?? [];
  const set = (i: number, patch: Partial<HardStopConfig>) =>
    update((c) => {
      c.rules = c.rules ?? {};
      const list = (c.rules.hardStops = c.rules.hardStops ?? []);
      list[i] = { ...list[i]!, ...patch };
    });
  const errFor = (i: number, f: string) => errorsFor(errors, `rules.hardStops.${i}.${f}`);
  const field = (i: number, f: string, label: string, control: ReactNode, hint?: string) => {
    const errs = errFor(i, f);
    return (
      <label className={s.field}>
        <span className={s.fieldLabel}>{label}</span>
        {control}
        {hint && <span className={s.fieldHint}>{hint}</span>}
        <FieldErrors errors={errs} id={`err-rules.hardStops.${i}.${f}`} />
      </label>
    );
  };
  return (
    <div className={s.stack}>
      <p className={s.muted}>
        Any hit sends the mail straight to a person (the You lane): a keyword match, or the decision engine
        answering the yes/no question above its threshold.
      </p>
      {stops.map((h, i) => (
        // Adding or removing re-keys every row, so field-local text re-reads the (shifted) config.
        <section
          key={`${i}:${stops.length}`}
          className={s.itemCard}
          aria-label={`Hard stop ${h.label || i + 1}`}
        >
          <div className={s.itemHead}>
            <span className={s.name}>{h.label || `Hard stop ${i + 1}`}</span>
            <Button
              size="sm"
              variant="ghost"
              className={s.push}
              aria-label={`Remove hard stop ${h.label || i + 1}`}
              onClick={() => update((c) => c.rules?.hardStops?.splice(i, 1))}
            >
              Remove
            </Button>
          </div>
          <div className={s.fields}>
            {field(
              i,
              'key',
              'Key',
              <Input
                className={cx('mono', errFor(i, 'key').length > 0 && s.invalid)}
                value={h.key}
                onChange={(e) => set(i, { key: e.target.value })}
              />,
            )}
            {field(
              i,
              'label',
              'Label',
              <Input
                className={cx(errFor(i, 'label').length > 0 && s.invalid)}
                value={h.label}
                onChange={(e) => set(i, { label: e.target.value })}
              />,
            )}
            {field(
              i,
              'threshold',
              'Threshold',
              <NumberInput
                value={h.threshold ?? 0.5}
                spec={{ min: 0.05, max: 0.95, step: 0.05 }}
                invalid={errFor(i, 'threshold').length > 0}
                onChange={(v) => set(i, { threshold: v })}
              />,
              'Probability above which the engine’s “yes” counts. 0.05–0.95; lower catches more.',
            )}
          </div>
          {field(
            i,
            'keywords',
            'Keywords',
            <Input
              defaultValue={(h.keywords ?? []).join(', ')}
              onChange={(e) => set(i, { keywords: listFromText(e.target.value) })}
            />,
            'Comma-separated. Any one of them stops the mail.',
          )}
          {field(
            i,
            'question',
            'Question for the decision engine',
            <Input value={h.question ?? ''} onChange={(e) => set(i, { question: e.target.value || null })} />,
          )}
        </section>
      ))}
      <div className={s.actions}>
        <Button
          size="sm"
          onClick={() =>
            update((c) => {
              c.rules = c.rules ?? {};
              (c.rules.hardStops = c.rules.hardStops ?? []).push({
                key: '',
                label: '',
                keywords: [],
                question: null,
                threshold: 0.5,
              });
            })
          }
        >
          + Add hard stop
        </Button>
      </div>
    </div>
  );
}

function JsonEditor({
  config,
  editable,
  onApply,
}: {
  config: DeploymentConfig;
  editable: boolean;
  onApply: (c: DeploymentConfig) => void;
}) {
  const [text, setText] = useState(() => JSON.stringify(config, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const apply = () => {
    try {
      const parsed = JSON.parse(text) as unknown;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
        throw new Error('The configuration must be a JSON object.');
      setParseError(null);
      onApply(parsed as DeploymentConfig);
    } catch (err) {
      setParseError(err instanceof Error ? err.message : 'This is not valid JSON.');
    }
  };
  return (
    <div className={s.stack}>
      <p className={s.muted}>
        The whole document: taxonomy, rules (hard stops, bucket overrides, priority), flow, models, thresholds
        and gates. The flow’s safety nodes (PII mask first, hard-stop guard, lane policy, approval gate last)
        are required; the server rejects a flow without them.
      </p>
      <label className={s.field}>
        <span className="sr-only">Configuration JSON</span>
        <TextArea
          className={s.json}
          spellCheck={false}
          value={text}
          readOnly={!editable}
          aria-invalid={parseError ? true : undefined}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      {parseError && (
        <div className={s.alert} role="alert">
          {parseError}
        </div>
      )}
      {editable && (
        <div className={s.actions}>
          <Button size="sm" variant="soft" onClick={apply}>
            Apply JSON to the draft
          </Button>
          <span className={s.muted}>Applying replaces the form values; save the draft to keep them.</span>
        </div>
      )}
    </div>
  );
}
