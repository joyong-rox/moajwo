import { useId, useState, type MouseEvent, type ReactNode } from 'react'
import { downloadRunResult, runDownloadUrl } from './api'

/** 실행 결과 내려받기 링크. 누르면(마우스·Enter·가운데 단추) 앱 안에서 받아 성공한 Excel 파일만 저장한다.
 *  결과 파일이 없거나(410) 세션·권한·서버 오류면 다른 화면(오류 JSON)으로 이동하지 않고 링크 옆에 이유를 보인다 —
 *  목록·검색 조건·키보드 초점이 그대로 남는다. 새 자료로 다시 실행할지는 사용자가 고른다 (자동으로 잇지 않음). */
export function RunDownloadLink({ runId, children }: { runId: number; children: ReactNode }) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const problemId = useId()

  const download = async (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setProblem(null)
    const outcome = await downloadRunResult(runId)
    setBusy(false)
    if (!outcome.ok) setProblem(outcome.message)
  }

  return (
    <>
      <a
        href={runDownloadUrl(runId)}
        onClick={(e) => void download(e)}
        onAuxClick={(e) => {
          if (e.button === 1) void download(e) // 가운데 단추로 새 탭에 열어도 오류 JSON 화면이 되지 않게
        }}
        aria-busy={busy || undefined}
        aria-describedby={problem ? problemId : undefined}
      >
        {children}
      </a>
      {problem && (
        <span id={problemId} className="upload-error run-download-problem" role="alert">
          {problem}
        </span>
      )}
    </>
  )
}
