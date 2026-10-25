/** More than the two buttons a toast has (`action` and `cancel`): passed as
 *  the toast's `action`, a row of them, each closing the toast on click. */
import { toast } from 'sonner'

export interface ToastButton {
  label: string
  onClick: () => void
}

interface ToastButtonsProps {
  buttons: ToastButton[]
  /** The toast to close once a button is clicked. */
  toastId?: string | number
}

export function ToastButtons({ buttons, toastId }: ToastButtonsProps) {
  return (
    <div className="ml-auto flex flex-wrap justify-end gap-1.5">
      {buttons.map((button, i) => (
        <button
          key={button.label}
          type="button"
          // Sonner styles its own action buttons by this attribute.
          data-button=""
          data-cancel={i > 0 ? '' : undefined}
          className="!ml-0"
          onClick={() => {
            if (toastId !== undefined) toast.dismiss(toastId)
            button.onClick()
          }}
        >
          {button.label}
        </button>
      ))}
    </div>
  )
}
