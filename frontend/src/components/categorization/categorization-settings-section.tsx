import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Sparkles } from 'lucide-react'
import { agents, categorization } from '@/lib/api'
import type { CategorizationSettings, CategorizationSettingsUpdate } from '@/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Segmented } from '@/components/invoice-ui'
import { CATEGORIZATION_SUMMARY_KEY, useCategorizationSummary } from '@/hooks/use-categorization-summary'
import { useFeatureFlags } from '@/hooks/use-feature-flags'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useWorkspace } from '@/contexts/workspace-context'

const SETTINGS_KEY = ['categorization', 'settings'] as const
const CONNECTIONS_KEY = ['agent-connections'] as const
const MAX_CAP = 1000

const PRESETS = [
  { value: '95', threshold: 95, labelKey: 'conservative' },
  { value: '90', threshold: 90, labelKey: 'balanced' },
  { value: '80', threshold: 80, labelKey: 'aggressive' },
] as const

/** Parses a cap typed by a person: 0..1000 with up to 2 decimals, "," or ".". */
function parseCap(raw: string): number | null {
  const text = raw.trim().replace(',', '.')
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null
  const value = Number(text)
  return value >= 0 && value <= MAX_CAP ? value : null
}

function usd(value: number, locale: string): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
  }).format(value)
}

export function CategorizationSettingsSection() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { hash } = useLocation()
  const sectionRef = useRef<HTMLElement>(null)
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const { canManage } = useWorkspace()
  const { agentsEnabled } = useFeatureFlags()
  const { data: summary } = useCategorizationSummary()

  const settingsQuery = useQuery({ queryKey: [...SETTINGS_KEY], queryFn: categorization.getSettings })
  const settings = settingsQuery.data

  // The connections list lives under agents: absent (404) when the module is off.
  const connectionsQuery = useQuery({
    queryKey: [...CONNECTIONS_KEY],
    queryFn: () => agents.connections.list(),
    enabled: agentsEnabled,
    retry: false,
  })
  const connections = connectionsQuery.isError ? [] : (connectionsQuery.data ?? [])
  const connection =
    connections.find((c) => c.id === settings?.llm_connection_id && c.kind === 'anthropic') ??
    connections.find((c) => c.kind === 'anthropic' && c.is_default)
  const connectionsAvailable = agentsEnabled && !connectionsQuery.isError

  const settingsReady = !!settings
  // The settings render async, so the browser's own hash scroll misses the
  // section: scroll once it exists, once per navigation to the anchor.
  useEffect(() => {
    if (hash === '#categorization' && settingsReady) {
      sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [hash, settingsReady])

  const update = useMutation({
    mutationFn: (patch: CategorizationSettingsUpdate) => categorization.updateSettings(patch),
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: [...SETTINGS_KEY] })
      const previous = qc.getQueryData<CategorizationSettings>([...SETTINGS_KEY])
      if (previous) qc.setQueryData<CategorizationSettings>([...SETTINGS_KEY], { ...previous, ...patch })
      return { previous }
    },
    onError: (_err, _patch, ctx) => {
      if (ctx?.previous) qc.setQueryData([...SETTINGS_KEY], ctx.previous)
      toast.error(t('categorization.config.saveError'))
    },
    onSuccess: (saved) => {
      qc.setQueryData([...SETTINGS_KEY], saved)
      qc.invalidateQueries({ queryKey: [...CATEGORIZATION_SUMMARY_KEY] })
    },
  })

  // What the person is typing; null shows the stored value.
  // One save at a time: a failed first PUT must not revert a later success.
  const busy = update.isPending
  const [capDraft, setCapDraft] = useState<string | null>(null)
  const [capError, setCapError] = useState(false)
  const storedCap = settings ? Number(settings.monthly_cap_usd) : null
  const capText = capDraft ?? (storedCap !== null && !Number.isNaN(storedCap) ? storedCap.toFixed(2) : '')

  const commitCap = () => {
    if (capDraft === null) return
    if (busy) return
    const parsed = parseCap(capDraft)
    if (parsed === null) {
      setCapError(true)
      return
    }
    setCapError(false)
    const text = capDraft
    setCapDraft(null)
    // A failed save gives the typed draft back instead of losing it.
    if (parsed !== storedCap) update.mutate({ monthly_cap_usd: parsed.toFixed(2) }, { onError: () => setCapDraft(text) })
  }

  const spent = Number(summary?.month_cost_usd ?? 0)
  const cap = storedCap ?? 0
  const pct = cap > 0 ? Math.min(100, (spent / cap) * 100) : spent > 0 ? 100 : 0
  const barTone = pct >= 100 ? 'bg-destructive' : pct >= 80 ? 'bg-amber-500' : 'bg-primary'

  const preset = PRESETS.find((p) => p.threshold === settings?.auto_apply_threshold)

  return (
    <section ref={sectionRef} id="categorization" aria-busy={busy} className="space-y-4 rounded-xl border bg-card p-6 scroll-mt-6">
      <div className="flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-base font-semibold">{t('categorization.config.title')}</h3>
      </div>

      {settingsQuery.isLoading ? (
        <div className="space-y-3" data-testid="categorization-settings-skeleton">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : !settings ? (
        <p className="text-sm text-muted-foreground">{t('categorization.config.loadError')}</p>
      ) : (
        <div className="space-y-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p id="categorization-enabled-label" className="text-sm font-medium">
                {t('categorization.config.enabled')}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">{t('categorization.config.enabledHint')}</p>
            </div>
            <Switch
              aria-labelledby="categorization-enabled-label"
              checked={settings.enabled}
              disabled={!canManage}
              onCheckedChange={(enabled) => {
                if (busy) return
                update.mutate({ enabled })
              }}
            />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">{t('categorization.config.threshold')}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t('categorization.config.thresholdHint', { value: settings.auto_apply_threshold })}
              </p>
            </div>
            <Segmented
              testIdPrefix="categorization-threshold"
              disabled={!canManage || !settings.enabled}
              // A stored value off the presets shows as its own selected chip.
              value={preset?.value ?? 'custom'}
              onChange={(value) => {
                if (value !== 'custom' && !busy) update.mutate({ auto_apply_threshold: Number(value) })
              }}
              options={[
                ...PRESETS.map((p) => ({ value: p.value as string, label: t(`categorization.config.${p.labelKey}`) })),
                ...(preset
                  ? []
                  : [{ value: 'custom', label: t('categorization.config.custom', { value: settings.auto_apply_threshold }) }]),
              ]}
            />
          </div>

          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <label htmlFor="categorization-cap" className="text-sm font-medium">
                {t('categorization.config.cap')}
              </label>
              <Input
                id="categorization-cap"
                inputMode="decimal"
                className="w-28 text-right"
                value={capText}
                disabled={!canManage}
                readOnly={busy}
                aria-invalid={capError}
                onChange={(e) => {
                  if (busy) return
                  setCapDraft(e.target.value)
                  setCapError(false)
                }}
                onBlur={commitCap}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitCap()
                }}
              />
            </div>
            {capError && (
              <p role="alert" className="text-xs text-destructive">
                {t('categorization.config.capInvalid', { max: MAX_CAP })}
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {t('categorization.config.spent', {
                spent: mask(usd(spent, locale)),
                cap: mask(usd(cap, locale)),
              })}
            </p>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
              <div className={`h-full rounded-full ${barTone}`} style={{ width: `${pct}%` }} />
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm">
              <span className="font-medium">{t('categorization.config.connection')}</span>{' '}
              {connection ? (
                <>
                  <span data-testid="categorization-connection-name">{connection.name}</span>
                  {' · '}
                  <Link to="/agents/connections" className="text-primary hover:underline">
                    {t('categorization.config.change')}
                  </Link>
                </>
              ) : connectionsAvailable ? (
                <Link to="/agents/connections" className="text-primary hover:underline">
                  {t('categorization.config.setUpConnection')}
                </Link>
              ) : (
                <span className="text-muted-foreground">{t('categorization.config.connectionUnavailable')}</span>
              )}
            </p>
            {canManage ? (
              <Button variant="outline" size="sm" asChild>
                <Link to="/categorization?tab=organize">{t('categorization.config.organize')}</Link>
              </Button>
            ) : (
              <Button variant="outline" size="sm" disabled>
                {t('categorization.config.organize')}
              </Button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
