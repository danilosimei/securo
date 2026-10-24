import { cn } from '@/lib/utils'

/** A key cap, shared by the review rows, the similar prompt and the legend. */
export function Kbd({ className, ...props }: React.ComponentProps<'kbd'>) {
  return (
    <kbd
      className={cn(
        'inline-flex h-4 min-w-4 items-center justify-center rounded border border-border bg-muted px-1 font-mono text-[10px] font-medium leading-none text-muted-foreground',
        className,
      )}
      {...props}
    />
  )
}
