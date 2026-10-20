import { useId, type ReactNode } from 'react'
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
  // 본문 설명 연결: 확인창마다 다른 id (여러 화면의 확인창끼리 섞이지 않음), 본문이 바뀌어도 같은 요소를 가리킨다
  const bodyId = useId()
  return (
    <AlertDialog
      isOpen={open}
      aria-describedby={bodyId}
      isKeyboardDismissDisabled={false}
      onOpenChange={(isOpen) => {
        if (!isOpen) onCancel()
      }}
    >
      <Heading slot="title" className="text-lg font-semibold text-title-50">
        {title}
      </Heading>
      <div id={bodyId} className="mt-2 text-sm leading-6 text-text-50">
        {children}
      </div>
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
