/** Mapa do Dinheiro: where the period's income went, group by group and down
 *  to the categories, and what was left (or how much was missing). A flow on
 *  wide screens, stacked bars on phones, and a table for anyone who prefers
 *  the numbers. */
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import type { MoneyMap, ReportPeriodParams } from '@/lib/api'
import { formatCurrency } from '@/lib/format'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatMoneyShort, formatPercent, useIsNarrow } from '@/components/reports/common/chart-utils'
import type { GroupPalette } from '@/components/reports/common/group-colors'
import { exploreLink, type ExploreTarget } from '@/components/reports/common/report-url'
import { NO_GROUP_LABEL } from '@/components/reports/explore/drilldown'
import { buildMapModel, moneyMapAnswer, type MapGroup, type MapItem, type MapModel } from './money-map-model'
import type { MapNode } from './money-map-graph'
import { MoneyMapSankey } from './money-map-sankey'
import { MoneyMapBars } from './money-map-bars'

/** The Explore filters behind a group, including the two rows without id. */
function groupTarget(group: MapGroup): Pick<ExploreTarget, 'group' | 'category'> {
  if (group.id) return { group: group.id }
  return group.label === NO_GROUP_LABEL ? { group: 'none' } : { category: 'none' }
}

interface MoneyMapViewProps {
  map: MoneyMap
  query: ReportPeriodParams
  currency: string
  locale: string
  palette: GroupPalette
  /** Toggles between the chart and the table. */
  view: 'chart' | 'table'
}

export function MoneyMapView({ map, query, currency, locale, palette, view }: MoneyMapViewProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { mask } = usePrivacyMode()
  const narrow = useIsNarrow()
  const [expanded, setExpanded] = useState(false)
  const model = useMemo(() => buildMapModel(map, expanded), [map, expanded])
  const money = (v: number) => mask(formatMoneyShort(v, currency, locale))
  const answer = moneyMapAnswer(model, t, money, locale)

  const go = (target: ExploreTarget, labels: { group?: string; category?: string }) => {
    const link = exploreLink(target, labels)
    navigate(link.to, { state: link.state })
  }
  const openGroup = (group: MapGroup) => go({ ...query, ...groupTarget(group) }, { group: group.label })
  const openCategory = (group: MapGroup, cat: MapItem) => {
    const scope = groupTarget(group)
    go(
      { ...query, group: scope.group ?? null, category: cat.id ?? 'none' },
      { group: group.label, category: cat.label },
    )
  }
  const onOpen = (node: MapNode) => {
    if (node.kind === 'group' && node.group) openGroup(node.group)
    if (node.kind === 'category' && node.group && node.item) openCategory(node.group, node.item)
  }

  if (model.totalIncome === 0 && model.totalSpending === 0) {
    return <p className="py-8 text-center text-sm text-muted-foreground">{t('reports.moneyMap.empty')}</p>
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground" data-testid="money-map-answer">{answer}</p>
      {view === 'table' ? (
        <MoneyMapTable model={buildMapModel(map, true)} currency={currency} locale={locale} />
      ) : narrow ? (
        <MoneyMapBars
          model={model}
          palette={palette}
          currency={currency}
          locale={locale}
          onOpenGroup={openGroup}
          onExpand={() => setExpanded(true)}
        />
      ) : (
        <div className="-mx-1 overflow-x-auto px-1">
          <div className="min-w-[640px]">
            <MoneyMapSankey
              model={model}
              palette={palette}
              currency={currency}
              locale={locale}
              onOpen={onOpen}
              onExpand={() => setExpanded(true)}
              ariaLabel={answer}
            />
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        {model.refunds > 0 ? (
          <p data-testid="money-map-refunds">{t('reports.moneyMap.refunds', { value: money(model.refunds) })}</p>
        ) : (
          <span />
        )}
        {expanded && view === 'chart' && (
          <button type="button" onClick={() => setExpanded(false)} className="font-medium text-primary hover:underline">
            {t('reports.moneyMap.collapse')}
          </button>
        )}
      </div>
    </div>
  )
}

function MoneyMapTable({ model, currency, locale }: { model: MapModel; currency: string; locale: string }) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const money = (v: number) => mask(formatCurrency(v, currency, locale))
  const ofIncome = (v: number) => (model.totalIncome > 0 ? formatPercent(v / model.totalIncome, locale) : '—')
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="money-map-table">
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th className="py-2 pr-2 font-medium">{t('reports.moneyMap.colItem')}</th>
            <th className="py-2 px-2 text-right font-medium">{t('reports.moneyMap.value')}</th>
            <th className="py-2 pl-2 text-right font-medium">{t('reports.moneyMap.ofIncome')}</th>
          </tr>
        </thead>
        <tbody className="tabular-nums">
          <tr className="border-b border-border">
            <th scope="row" className="py-2 pr-2 text-left font-semibold">{t('reports.moneyMap.income')}</th>
            <td className="py-2 px-2 text-right font-semibold">{money(model.totalIncome)}</td>
            <td className="py-2 pl-2 text-right text-muted-foreground">{model.totalIncome > 0 ? '100%' : '—'}</td>
          </tr>
          {model.income.map((i) => (
            <tr key={`in:${i.id ?? i.label}`} className="border-b border-border/60">
              <td className="py-1.5 pl-4 pr-2">{i.label}</td>
              <td className="py-1.5 px-2 text-right">{money(i.value)}</td>
              <td className="py-1.5 pl-2 text-right text-muted-foreground">{ofIncome(i.value)}</td>
            </tr>
          ))}
          <tr className="border-b border-border">
            <th scope="row" className="py-2 pr-2 text-left font-semibold">{t('reports.moneyMap.spending')}</th>
            <td className="py-2 px-2 text-right font-semibold">{money(model.totalSpending)}</td>
            <td className="py-2 pl-2 text-right text-muted-foreground">{ofIncome(model.totalSpending)}</td>
          </tr>
          {model.groups.map((g) => (
            <GroupRows key={`g:${g.id ?? g.label}`} group={g} money={money} ofIncome={ofIncome} />
          ))}
          <tr>
            <th scope="row" className="py-2 pr-2 text-left font-semibold">
              {t(model.left < 0 ? 'reports.moneyMap.short' : 'reports.moneyMap.left')}
            </th>
            <td className="py-2 px-2 text-right font-semibold">{money(Math.abs(model.left))}</td>
            <td className="py-2 pl-2 text-right text-muted-foreground">{ofIncome(Math.abs(model.left))}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

function GroupRows({ group, money, ofIncome }: { group: MapGroup; money: (v: number) => string; ofIncome: (v: number) => string }) {
  return (
    <>
      <tr className="border-b border-border/60">
        <td className="py-1.5 pl-4 pr-2 font-medium">{group.label}</td>
        <td className="py-1.5 px-2 text-right">{money(group.value)}</td>
        <td className="py-1.5 pl-2 text-right text-muted-foreground">{ofIncome(group.value)}</td>
      </tr>
      {/* A group whose only category carries its own name adds nothing. */}
      {(group.categories.length === 1 && group.categories[0].label === group.label ? [] : group.categories).map((c) => (
        <tr key={c.id ?? c.label} className="border-b border-border/40 text-muted-foreground">
          <td className="py-1 pl-8 pr-2">{c.label}</td>
          <td className="py-1 px-2 text-right">{money(c.value)}</td>
          <td className="py-1 pl-2 text-right">{ofIncome(c.value)}</td>
        </tr>
      ))}
    </>
  )
}
