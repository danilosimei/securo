import { useState } from 'react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { ListSkeleton } from '@/components/ui/list-skeleton'
import { SectionCard } from '@/components/ui/section-card'
import { CLICKABLE_ROW_CLASS, clickableRow } from '@/components/ui/clickable-row'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, Plug, Plus, Star, XCircle, Zap } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { RowActions } from '@/components/ui/row-actions'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { PageHeader } from '@/components/page-header'
import { agents } from '@/lib/api'
import type { LlmConnection } from '@/lib/api'
import { ConnectionFormDialog } from '@/components/agents/connection-form-dialog'
import { McpExternalPanel } from '@/components/agents/mcp-external-panel'
import { useWorkspace } from '@/contexts/workspace-context'

export default function AgentConnectionsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { canWrite } = useWorkspace()
  const [editing, setEditing] = useState<LlmConnection | null>(null)
  const [deleting, setDeleting] = useState<LlmConnection | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; detail: string }>>({})

  const { data: list, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['agent-connections'],
    queryFn: () => agents.connections.list(),
  })

  const removeMut = useMutation({
    mutationFn: (id: string) => agents.connections.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['agent-connections'] })
      toast.success(t('agents.connections.deleted'))
      setDeleting(null)
    },
  })

  const testMut = useMutation({
    mutationFn: (id: string) => agents.connections.test(id),
    onSuccess: (res, id) => {
      setTestResults((r) => ({ ...r, [id]: { ok: res.ok, detail: res.detail } }))
    },
  })

  return (
    <div>
      <PageHeader
        back={{ to: '/agents', label: t('agents.title') }}
        section={t('agents.title')}
        title={t('agents.connections.title')}
        action={
          canWrite ? (
            <Button size="sm" className="gap-1.5 h-8" onClick={() => setCreateOpen(true)}>
              <Plus size={13} /> {t('agents.connections.add')}
            </Button>
          ) : undefined
        }
      />

      <p className="text-sm text-muted-foreground mb-6 max-w-2xl">{t('agents.connections.subtitle')}</p>

      {isError ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <SectionCard><ListSkeleton /></SectionCard>
      ) : (list?.length ?? 0) === 0 ? (
        <SectionCard>
          <EmptyState
            icon={Plug}
            title={t('agents.connections.empty.title')}
            hint={t('agents.connections.empty.subtitle')}
            action={
              canWrite ? (
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus /> {t('agents.connections.add')}
                </Button>
              ) : undefined
            }
          />
        </SectionCard>
      ) : (
        <SectionCard>
          <div className="divide-y divide-border">
            {list?.map((c) => {
              const tested = testResults[c.id]
              return (
                <div
                  key={c.id}
                  {...clickableRow(canWrite ? () => setEditing(c) : undefined)}
                  className={`flex items-start gap-3 px-4 sm:px-5 py-4 hover:bg-muted/50 transition-colors ${canWrite ? CLICKABLE_ROW_CLASS : ''}`}
                >
                  <div className="h-10 w-10 rounded-md bg-muted flex items-center justify-center shrink-0">
                    <Plug className="h-5 w-5 text-muted-foreground" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-semibold truncate">{c.name}</span>
                      <Badge variant="secondary" className="text-[10px] uppercase tracking-wider px-1.5 py-0">
                        {c.kind}
                      </Badge>
                      {c.is_default && (
                        <Badge className="text-[10px] uppercase tracking-wider px-1.5 py-0 bg-amber-100 hover:bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-200 inline-flex items-center gap-1">
                          <Star className="h-3 w-3" /> {t('agents.connections.default')}
                        </Badge>
                      )}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5 truncate">
                      {c.base_url || t('agents.connections.providerDefaultEndpoint')} ·{' '}
                      {c.default_model || t('agents.connections.noDefaultModel')}
                    </div>
                    {tested && (
                      <div
                        className={`mt-2 inline-flex items-center gap-1.5 text-xs ${
                          tested.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'
                        }`}
                      >
                        {tested.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
                        {tested.detail}
                      </div>
                    )}
                  </div>
                  {canWrite && (
                    <RowActions
                      label={c.name}
                      onEdit={() => setEditing(c)}
                      onDelete={() => setDeleting(c)}
                      extra={[{
                        label: t('agents.connections.test'),
                        icon: Zap,
                        onSelect: () => testMut.mutate(c.id),
                        disabled: testMut.isPending,
                      }]}
                    />
                  )}
                </div>
              )
            })}
          </div>
        </SectionCard>
      )}

      <McpExternalPanel />

      <DeleteConfirmationDialog
        open={!!deleting}
        title={t('common.delete')}
        description={t('agents.connections.deleteConfirm', { name: deleting?.name })}
        isPending={removeMut.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && removeMut.mutate(deleting.id)}
      />

      <ConnectionFormDialog open={createOpen} onOpenChange={setCreateOpen} />
      <ConnectionFormDialog
        open={!!editing}
        onOpenChange={(o) => !o && setEditing(null)}
        connection={editing}
      />
    </div>
  )
}
