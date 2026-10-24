import { cn } from "@/lib/utils"

/**
 * Content swapped by a tab or a view switch. Each new `panelKey` mounts a
 * fresh panel that fades in, instead of the old one being replaced at once.
 * Without motion (reduced-motion users) it simply swaps.
 */
function FadePanel({
  panelKey,
  className,
  ...props
}: React.ComponentProps<"div"> & { panelKey: string }) {
  return (
    <div
      key={panelKey}
      data-slot="fade-panel"
      className={cn("motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-200", className)}
      {...props}
    />
  )
}

export { FadePanel }
