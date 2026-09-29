/**
 * The model nodes of a deployment version and the agent that runs each: provider, model, prompt, limits,
 * and (for the drafter) the house style and sign-off. Empty fields inherit from the deployment's defaults,
 * then the platform's. Only providers the workspace's model policy allows can be chosen; the server checks
 * again on save, and publishing also needs the provider installed.
 */
import type { ModelPolicyDTO } from '@ci/contracts';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../lib/api';
import { keys } from '../../../lib/queries';
import { Input, TextArea } from '../../../ui';
import { Select } from '../bits';
import {
  errorsUnder,
  MODEL_NODES,
  type ConfigError,
  type DeploymentConfig,
  type NodeAgentConfig,
  type ProviderKey,
} from '../model';
import s from '../admin.module.css';

type Update = (fn: (c: DeploymentConfig) => void) => void;

export function AgentsSection({
  config,
  update,
  errors,
}: {
  config: DeploymentConfig;
  update: Update;
  errors: ConfigError[];
}) {
  const policy = useQuery({
    queryKey: keys.modelPolicy,
    queryFn: () => api.get<ModelPolicyDTO>('/v1/workspace/model-policy'),
  }).data;
  const providers = policy?.providers ?? [];
  const nodes = config.flow?.nodes ?? [];
  const models = config.models ?? {};
  const platformDefault = providers.find((p) => p.isDefault);
  const defaultModel = (key: ProviderKey) =>
    providers.find((p) => p.key === (key || platformDefault?.key))?.defaultModel ?? '';

  const setModels = (patch: Record<string, unknown>) =>
    update((c) => {
      c.models = { ...(c.models ?? {}), ...patch };
    });
  const setAgent = (index: number, patch: Partial<NodeAgentConfig>) =>
    update((c) => {
      const node = c.flow!.nodes[index]!;
      node.agent = { ...(node.agent ?? {}), ...patch };
    });

  const providerOptions = (current: ProviderKey | undefined, inheritLabel: string) => (
    <>
      <option value="">{inheritLabel}</option>
      {providers.map((p) => (
        <option key={p.key} value={p.key} disabled={!p.allowed && current !== p.key}>
          {p.name}
          {!p.allowed ? ' — not allowed by the model policy' : !p.configured ? ' — not installed' : ''}
        </option>
      ))}
    </>
  );
  const deploymentProvider = (models.system2Provider ?? '') as ProviderKey;

  return (
    <div className={s.stack}>
      <p className={s.muted}>
        Classification runs on the model hosted in your environment. These nodes use a language model: each
        names its agent here. Personal data is masked before any model sees the mail, and a person approves
        every reply.
      </p>
      <section className={s.itemCard} aria-label="Deployment defaults">
        <div className={s.itemHead}>
          <span className={s.name}>Defaults for every model node</span>
        </div>
        <div className={s.fields}>
          <label className={s.field}>
            <span className={s.fieldLabel}>Provider</span>
            <Select
              value={deploymentProvider}
              onChange={(e) => setModels({ system2Provider: e.target.value as ProviderKey })}
            >
              {providerOptions(deploymentProvider, `Platform default (${platformDefault?.name ?? 'none'})`)}
            </Select>
          </label>
          <label className={s.field}>
            <span className={s.fieldLabel}>Model</span>
            <Input
              className="mono"
              value={String(models.system2Model ?? '')}
              placeholder={defaultModel(deploymentProvider)}
              maxLength={120}
              onChange={(e) => setModels({ system2Model: e.target.value.trim() })}
            />
            <span className={s.fieldHint}>Blank: the provider's default model.</span>
          </label>
          <label className={s.field}>
            <span className={s.fieldLabel}>Budget per mail (minor units)</span>
            <Input
              type="number"
              min={0}
              max={10000}
              value={String(models.maxCostMinorPerMail ?? 50)}
              onChange={(e) => setModels({ maxCostMinorPerMail: Number(e.target.value) })}
            />
            <span className={s.fieldHint}>
              Past it, the rest of the mail's stages fall back and a person takes over.
            </span>
          </label>
        </div>
      </section>

      {MODEL_NODES.map((spec) => {
        const index = nodes.findIndex((n) => n.type === spec.type);
        if (index < 0) return null;
        const a = nodes[index]!.agent ?? {};
        const base = `flow.nodes.${index}.agent`;
        const provider = (a.provider ?? '') as ProviderKey;
        const effective = provider || deploymentProvider;
        return (
          <section key={spec.type} className={s.itemCard} aria-label={`${spec.title} agent`}>
            <div className={s.itemHead}>
              <span className={s.name}>{a.name || spec.title}</span>
              <span className={s.muted} style={{ marginLeft: 8 }}>
                {spec.what}
              </span>
            </div>
            <div className={s.fields}>
              <label className={s.field}>
                <span className={s.fieldLabel}>Name</span>
                <Input
                  value={a.name ?? ''}
                  placeholder={spec.title}
                  maxLength={80}
                  onChange={(e) => setAgent(index, { name: e.target.value })}
                />
              </label>
              <label className={s.field}>
                <span className={s.fieldLabel}>Provider</span>
                <Select
                  aria-label={`${spec.title} provider`}
                  value={provider}
                  onChange={(e) => setAgent(index, { provider: e.target.value as ProviderKey })}
                >
                  {providerOptions(provider, 'Deployment default')}
                </Select>
              </label>
              <label className={s.field}>
                <span className={s.fieldLabel}>Model</span>
                <Input
                  className="mono"
                  value={a.model ?? ''}
                  placeholder={String(models.system2Model || defaultModel(effective))}
                  maxLength={120}
                  onChange={(e) => setAgent(index, { model: e.target.value.trim() })}
                />
              </label>
              <label className={s.field}>
                <span className={s.fieldLabel}>Max output tokens</span>
                <Input
                  type="number"
                  min={256}
                  max={32000}
                  value={a.maxTokens == null ? '' : String(a.maxTokens)}
                  placeholder="Default"
                  onChange={(e) =>
                    setAgent(index, { maxTokens: e.target.value ? Number(e.target.value) : null })
                  }
                />
              </label>
              <label className={s.field}>
                <span className={s.fieldLabel}>Reasoning effort</span>
                <Select
                  value={a.effort ?? ''}
                  onChange={(e) =>
                    setAgent(index, { effort: (e.target.value || null) as NodeAgentConfig['effort'] })
                  }
                >
                  <option value="">Model default</option>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </Select>
              </label>
              <label className={s.field}>
                <span className={s.fieldLabel}>Price per 1,000 tokens (minor units)</span>
                <Input
                  type="number"
                  min={0}
                  value={a.costPer1kMinor == null ? '' : String(a.costPer1kMinor)}
                  placeholder="Platform price list"
                  onChange={(e) =>
                    setAgent(index, { costPer1kMinor: e.target.value ? Number(e.target.value) : null })
                  }
                />
                <span className={s.fieldHint}>Used for budgets and cost reports.</span>
              </label>
            </div>
            <label className={s.field} style={{ marginTop: 10 }}>
              <span className={s.fieldLabel}>Instructions (system prompt)</span>
              <TextArea
                rows={5}
                value={a.prompt ?? ''}
                maxLength={12000}
                placeholder="Blank: the built-in instructions for this node."
                onChange={(e) => setAgent(index, { prompt: e.target.value })}
              />
            </label>
            {spec.type === 'draft_reply' && (
              <div className={s.two} style={{ marginTop: 10 }}>
                <label className={s.field}>
                  <span className={s.fieldLabel}>House style</span>
                  <TextArea
                    rows={4}
                    value={a.styleGuide ?? ''}
                    maxLength={4000}
                    placeholder="e.g. British spelling; no exclamation marks; address the customer by name."
                    onChange={(e) => setAgent(index, { styleGuide: e.target.value })}
                  />
                </label>
                <label className={s.field}>
                  <span className={s.fieldLabel}>Sign-off</span>
                  <TextArea
                    rows={4}
                    value={a.signature ?? ''}
                    maxLength={600}
                    placeholder={'Kind regards,\nCustomer Care'}
                    onChange={(e) => setAgent(index, { signature: e.target.value })}
                  />
                </label>
              </div>
            )}
            {errorsUnder(errors, base).length > 0 && (
              <span className={s.fieldError}>
                {errorsUnder(errors, base)
                  .map((e) => `${e.path.slice(base.length + 1) || 'agent'}: ${e.message}`)
                  .join(' ')}
              </span>
            )}
          </section>
        );
      })}
    </div>
  );
}
