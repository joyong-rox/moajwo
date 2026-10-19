import type { ReactNode } from 'react'
import { Heading } from 'react-aria-components'
import { AlertDialog } from './components/tailgrids/core/alert-dialog'
import { Button } from './components/tailgrids/core/button'

/** 되돌리기 어려운 동작 확인 (Tailgrids Alert Dialog). 바깥을 눌러도 닫히지 않고, '취소'(처음 포커스·Esc)나 확인 버튼을 골라야 닫힌다. */
function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  danger = false,
  onConfirm,
  onCancel,
}: {
  open: boolean
  title: string
  children: ReactNode
  confirmLabel: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <AlertDialog
      isOpen={open}
      isKeyboardDismissDisabled={false}
      onOpenChange={(isOpen) => {
        if (!isOpen) onCancel()
      }}
    >
      <Heading slot="title" className="text-lg font-semibold text-title-50">
        {title}
      </Heading>
      <div className="mt-2 text-sm leading-6 text-text-50">{children}</div>
      <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button appearance="outline" size="sm" autoFocus onPress={onCancel}>
          취소
        </Button>
        <Button variant={danger ? 'danger' : 'primary'} size="sm" onPress={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </AlertDialog>
  )
}

export default ConfirmDialog
