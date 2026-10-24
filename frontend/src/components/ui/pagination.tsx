import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

/**
 * The strip at the foot of a paged list, inside its card: Previous, "2 / 9",
 * Next, and how many rows to show. Shown only when there is more than one
 * page's worth of rows to choose from, so callers decide with `total`.
 * @example `<Pagination page={page} pageSize={limit} total={data.total} onPageChange={setPage} onPageSizeChange={setLimit} />`
 */
export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizes = [10, 20, 50, 100],
  className,
}: {
  page: number
  pageSize: number
  /** Number of rows across all pages. */
  total: number
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
  pageSizes?: number[]
  className?: string
}) {
  const { t } = useTranslation()
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  if (total <= Math.min(...pageSizes)) return null
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-between gap-3 border-t border-border px-4 py-3 sm:flex-row sm:px-5',
        className,
      )}
    >
      {pageCount > 1 ? (
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
            {t('common.previous')}
          </Button>
          <span className="text-sm tabular-nums text-muted-foreground">
            {page} / {pageCount}
          </span>
          <Button variant="outline" size="sm" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>
            {t('common.next')}
          </Button>
        </div>
      ) : (
        <div className="hidden sm:block" />
      )}
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">{t('common.rowsPerPage')}</span>
        <Select
          value={String(pageSize)}
          onValueChange={(value) => {
            onPageSizeChange(Number(value))
            onPageChange(1)
          }}
        >
          <SelectTrigger className="h-8 w-[70px] text-xs">
            <SelectValue placeholder={pageSize} />
          </SelectTrigger>
          <SelectContent>
            {pageSizes.map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}
