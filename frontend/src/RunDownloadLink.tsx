import { useEffect, useId, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { downloadRunResult, runDownloadUrl } from './api'

/** 실행 결과 내려받기 링크. 누르면(마우스·Enter·가운데 단추) 앱 안에서 받아 성공한 Excel 파일만 저장한다.
 *  결과 파일이 없거나(410) 세션·권한·서버 오류면 다른 화면(오류 JSON)으로 이동하지 않고 링크 옆에 이유를 보인다 —
 *  목록·검색 조건·키보드 초점이 그대로 남는다. 새 자료로 다시 실행할지는 사용자가 고른다 (자동으로 잇지 않음).
 *  받는 중에 링크가 화면에서 내려가면(다른 메뉴·로그아웃·세션 만료) 요청을 취소하고, 늦게 도착한 파일은 저장하지 않는다.
 *  받기가 어떻게 끝나든 링크는 다시 누를 수 있다 — 다시 받기는 사용자가 누를 때만 한다. */
export function RunDownloadLink({ runId, children }: { runId: number; children: ReactNode }) {
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const problemId = useId()
  // 받는 중인 요청 — 같은 처리 안에서 두 번 눌려도(다시 그리기 전) 바로 막고, 화면에서 내려가면 취소한다
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  const download = async (event: MouseEvent<HTMLAnchorElement>) => {
    event.preventDefault()
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setProblem(null)
    try {
      const outcome = await downloadRunResult(runId, controller.signal)
      if (!outcome.ok && !controller.signal.aborted) setProblem(outcome.message)
    } catch {
      // 저장 단계 등 예상 밖 오류도 링크를 '받는 중'에 묶어 두지 않고 이유를 보인다
      if (!controller.signal.aborted) setProblem('내려받지 못했습니다 — 다시 내려받으세요.')
    } finally {
      pending.current = null
      setBusy(false)
    }
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
