import type { Asset, AssetGroup } from '@/types'

/** Asset types that are things owned, not investments ("Só investimentos"
 *  leaves them out). */
export const NON_INVESTMENT_TYPES: ReadonlySet<string> = new Set(['real_estate', 'vehicle', 'valuable'])

/** One on/off chip of the Assets page "Somar" filter: a wallet, or an asset
 *  that sits in no wallet. */
export interface HoldingChip {
  key: string
  label: string
  kind: 'wallet' | 'asset'
  /** Wallet color; null for an asset. */
  color: string | null
  /** Asset type; null for a wallet. */
  type: string | null
}

/** What the filter turns an asset on or off by: its wallet, or itself. */
export function holdingKey(asset: Pick<Asset, 'id' | 'group_id'>): string {
  return asset.group_id ?? asset.id
}

/** The chips, in the order the page lists them: wallets that hold something,
 *  then the assets outside any wallet. Sold assets get none. */
export function holdingChips(
  wallets: Pick<AssetGroup, 'id' | 'name' | 'color'>[],
  activeAssets: Pick<Asset, 'id' | 'name' | 'type' | 'group_id'>[],
): HoldingChip[] {
  const used = new Set(activeAssets.map((a) => a.group_id).filter(Boolean))
  return [
    ...wallets
      .filter((w) => used.has(w.id))
      .map((w) => ({ key: w.id, label: w.name, kind: 'wallet' as const, color: w.color, type: null })),
    ...activeAssets
      .filter((a) => !a.group_id)
      .map((a) => ({ key: a.id, label: a.name, kind: 'asset' as const, color: null, type: a.type })),
  ]
}

/** The keys "Só investimentos" leaves off: assets outside a wallet that are
 *  property, vehicles or valuables. */
export function nonInvestmentKeys(chips: HoldingChip[]): string[] {
  return chips.filter((c) => c.kind === 'asset' && c.type && NON_INVESTMENT_TYPES.has(c.type)).map((c) => c.key)
}

export interface PortfolioTrend {
  assets: { id: string; name: string; type: string; group_id: string | null }[]
  trend: Record<string, unknown>[]
  total: number
}

/** The portfolio trend with only the assets `keep` lets through. Trend rows
 *  are keyed by asset id, so the other columns are dropped and each row's
 *  `_total` (and the closing `total`) is summed again. */
export function scopePortfolio<T extends PortfolioTrend>(
  data: T,
  keep: (asset: T['assets'][number]) => boolean,
): T {
  const keptAssets = data.assets.filter(keep)
  const keptIds = new Set(keptAssets.map((a) => a.id))
  const trend = data.trend.map((row) => {
    const next: Record<string, unknown> = { date: (row as { date: unknown }).date }
    let total = 0
    for (const [k, v] of Object.entries(row)) {
      if (k === 'date' || k === '_total') continue
      if (keptIds.has(k)) {
        next[k] = v
        total += Number(v) || 0
      }
    }
    next._total = total
    return next
  })
  const lastTotal = trend.length ? Number((trend[trend.length - 1] as { _total?: number })._total) || 0 : 0
  return { ...data, assets: keptAssets, trend, total: lastTotal }
}
