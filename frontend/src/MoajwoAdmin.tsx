import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  ACCOUNT_STATUS_LABEL,
  changeUserStatus,
  createUser,
  fetchAdminOverview,
  fetchAudit,
  listUsers,
  resetPassword,
  type AccountStatus,
  type AdminOverview,
  type AuditEvent,
  type StatusAction,
  type UserAccount,
} from "./api";
import { navigate } from "./router";
import { ADMIN_BASE } from "./routes";
import { formatTime } from "./runFormat";

const TABS = [
  { path: ADMIN_BASE, label: "관리 현황" },
  { path: `${ADMIN_BASE}/users`, label: "사용자·가입 신청" },
  { path: `${ADMIN_BASE}/audit`, label: "관리 이력" },
];

/** 사용자 목록은 분 단위까지 (표 폭을 줄인다). 관리 이력은 초 단위 그대로 */
const minute = (value: string | null) => formatTime(value).slice(0, 16);

const message = (err: unknown) =>
  err instanceof Error ? err.message : String(err);

/** 최고 관리자 전용 관리 화면 (/moajwoAdmin). 서버도 모든 관리 API를 최고 관리자만 허용한다. */
function MoajwoAdmin({ path }: { path: string }) {
  const current = TABS.find((t) => t.path === path) ?? TABS[0];
  return (
    <>
      <h1 className="page-title">관리자 · {current.label}</h1>
      <nav className="admin-tabs" aria-label="관리자 메뉴">
        {TABS.map((t) => (
          <a
            key={t.path}
            href={t.path}
            className={`admin-filter${t.path === path ? " is-active" : ""}`}
            aria-current={t.path === path ? "page" : undefined}
            onClick={(e) => {
              if (
                e.button !== 0 ||
                e.metaKey ||
                e.ctrlKey ||
                e.shiftKey ||
                e.altKey
              )
                return;
              e.preventDefault();
              navigate(t.path);
            }}
          >
            {t.label}
          </a>
        ))}
      </nav>
      {path === ADMIN_BASE && <Overview />}
      {path === `${ADMIN_BASE}/users` && <UserAdmin />}
      {path === `${ADMIN_BASE}/audit` && <Audit />}
    </>
  );
}

// ---------------------------------------------------------------- 관리 현황

function kstToday(): string {
  return new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);
}

function Card({
  label,
  value,
  children,
}: {
  label: string;
  value: number;
  children?: ReactNode;
}) {
  return (
    <div className="dash-card">
      <dt>{label}</dt>
      <dd className="dash-value">{value.toLocaleString("ko-KR")}</dd>
      {children && <dd className="dash-sub">{children}</dd>}
    </div>
  );
}

function Overview() {
  const today = kstToday();
  const [start, setStart] = useState(`${today.slice(0, 8)}01`);
  const [end, setEnd] = useState(today);
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchPeriod = useCallback((s: string, e: string) => {
    fetchAdminOverview(s, e)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((err: unknown) => setError(message(err)));
  }, []);

  useEffect(() => {
    fetchPeriod(`${kstToday().slice(0, 8)}01`, kstToday());
  }, [fetchPeriod]);

  const load = (s: string, e: string) => {
    setError(null);
    fetchPeriod(s, e);
  };

  const count = (s: AccountStatus) =>
    data?.users.find((u) => u.status === s)?.count ?? 0;

  return (
    <section className="admin" aria-label="관리 현황">
      <h2>관리 현황</h2>
      <form
        className="admin-form admin-period"
        onSubmit={(e) => {
          e.preventDefault();
          load(start, end);
        }}
      >
        <label>
          시작일
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            required
          />
        </label>
        <label>
          종료일
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            required
          />
        </label>
        <button type="submit" className="mapping-run">
          기간 조회
        </button>
      </form>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <h3>사용자 (지금 시점)</h3>
          <dl className="dash-cards">
            <Card label="승인 대기 사용자" value={count("pending")}>
              {count("pending") > 0 && (
                <a
                  href={`${ADMIN_BASE}/users?status=pending`}
                  onClick={(e) => {
                    e.preventDefault();
                    navigate(`${ADMIN_BASE}/users?status=pending`);
                  }}
                >
                  가입 신청 처리하기
                </a>
              )}
            </Card>
            <Card label="이용 중 사용자" value={count("active")}>
              최고 관리자 {data.owners}명 포함
            </Card>
            <Card label="이용 중지 사용자" value={count("suspended")}>
              가입 반려 {count("rejected")}명은 따로 셉니다
            </Card>
          </dl>
          <h3>
            선택 기간 {data.start} ~ {data.end}
          </h3>
          <dl className="dash-cards">
            <Card label="업로드 파일" value={data.uploads.total}>
              이용 중이 아닌 계정의 파일 {data.uploads.inactive_accounts}개 포함
            </Card>
            <Card label="통합 실행" value={data.runs.total}>
              완료 {data.runs.done} · 실패 {data.runs.failed} · 검토 대기{" "}
              {data.runs.review}
              {data.runs.running > 0 ? ` · 실행 중 ${data.runs.running}` : ""}
            </Card>
          </dl>
          <details className="admin-help">
            <summary>집계 기준</summary>
            <ul>
              <li>
                기간: 시작일 00:00 ~ 종료일 24:00, {data.timezone}. 기본값은
                이번 달 1일 ~ 오늘입니다. 최대 366일.
              </li>
              <li>
                사용자 수는 기간과 관계없이 지금 시점의 계정 상태별 수입니다.
              </li>
              <li>
                업로드 파일: 기간 안에 올린 원본 파일 수 (같은 파일을 다시
                올리면 따로 셉니다).
              </li>
              <li>
                통합 실행: 기간 안에 시작한 실행 기록(직접 검토·자동화) — 홈
                대시보드의 '이번 달 실행 횟수'와 같은 시작 시각 기준. 검토
                대기는 확인할 항목이 남아 결과 파일을 아직 만들지 않은
                실행입니다.
              </li>
              <li>
                포함 범위: 계정 상태와 관계없이 모든 기록을 셉니다. 이용 중지된
                검증용 계정의 기록도 빼지 않습니다 (이 기간: 업로드{" "}
                {data.uploads.inactive_accounts}개 · 실행{" "}
                {data.runs.inactive_accounts}건이 이용 중이 아닌 계정의 기록,
                계정 도입 전 기록은 업로드 {data.uploads.no_owner}개 · 실행{" "}
                {data.runs.no_owner}건).
              </li>
              <li>
                최근 로그인과 실제 업무 활동(업로드·검증·검토·통합)은
                '사용자·가입 신청' 화면에서 따로 보여줍니다.
              </li>
              <li>절약 시간 같은 추정치는 이 화면에 표시하지 않습니다.</li>
            </ul>
            <p className="dash-sub">
              집계 시각: {formatTime(data.generated_at)}
            </p>
          </details>
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- 사용자·가입 신청

const STATUS_BADGE: Record<AccountStatus, string> = {
  pending: "is-pending",
  active: "is-approved",
  rejected: "is-rejected",
  suspended: "is-suspended",
};

type Pending =
  | { kind: "status"; user: UserAccount; action: StatusAction }
  | { kind: "password"; user: UserAccount };

const ACTION: Record<
  StatusAction,
  { label: string; result: AccountStatus; reasonHint: string }
> = {
  approve: { label: "가입 승인", result: "active", reasonHint: "메모 (선택)" },
  reject: {
    label: "가입 반려",
    result: "rejected",
    reasonHint: "반려 사유 (관리 이력에 남습니다)",
  },
  suspend: {
    label: "이용 중지",
    result: "suspended",
    reasonHint: "중지 사유 (관리 이력에 남습니다)",
  },
  resume: { label: "이용 재개", result: "active", reasonHint: "메모 (선택)" },
};

const ACTIONS_FOR: Record<AccountStatus, StatusAction[]> = {
  pending: ["approve", "reject"],
  active: ["suspend"],
  suspended: ["resume"],
  rejected: [],
};

function initialFilter(): AccountStatus | "all" {
  const s = new URLSearchParams(window.location.search).get("status");
  return s && s in ACCOUNT_STATUS_LABEL ? (s as AccountStatus) : "all";
}

function UserAdmin() {
  const [users, setUsers] = useState<UserAccount[] | null>(null);
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [filter, setFilter] = useState<AccountStatus | "all">(initialFilter);
  const [pending, setPending] = useState<Pending | null>(null);
  const [reason, setReason] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPassword2, setNewPassword2] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback((q: string) => {
    return listUsers({ q: q || undefined })
      .then(setUsers)
      .catch((err: unknown) => setError(message(err)));
  }, []);

  useEffect(() => {
    void load("");
  }, [load]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: users?.length ?? 0 };
    for (const u of users ?? []) c[u.status] = (c[u.status] ?? 0) + 1;
    return c;
  }, [users]);
  const shown = (users ?? []).filter(
    (u) => filter === "all" || u.status === filter,
  );

  function open(next: Pending) {
    setPending(next);
    setReason("");
    setNewPassword("");
    setNewPassword2("");
    setError(null);
    setNotice(null);
  }

  async function confirm() {
    if (!pending || busy) return;
    setBusy(true);
    setError(null);
    const { user } = pending;
    try {
      if (pending.kind === "status") {
        const updated = await changeUserStatus(
          user.id,
          pending.action,
          reason.trim() || null,
        );
        setNotice(
          `'${user.username}' ${ACTION[pending.action].label} 완료 — 현재 상태: ${updated.status_label}`,
        );
      } else {
        if (newPassword !== newPassword2)
          throw new Error("새 비밀번호 확인이 일치하지 않습니다.");
        await resetPassword(user.id, newPassword);
        setNotice(
          `'${user.username}' 비밀번호를 초기화했습니다. 기존 로그인은 모두 종료되었습니다.`,
        );
      }
      setPending(null);
    } catch (err) {
      setError(message(err));
    } finally {
      // 성공·실패 모두 서버의 실제 상태로 목록을 다시 그린다 (중복 클릭·오래된 화면 대비)
      await load(applied);
      setBusy(false);
    }
  }

  return (
    <section className="admin admin-wide" aria-label="사용자·가입 신청 관리">
      <h2>사용자·가입 신청</h2>
      <p className="upload-drop-hint">
        가입 신청은 일반 사용자·승인 대기로 들어옵니다. 승인해야 로그인·업무
        기능을 쓸 수 있습니다. 가입 반려(신청을 받지 않음)와 이용 중지(쓰던
        계정을 멈춤)는 다른 상태이며, 반려한 신청은 다시 승인할 수 없습니다.
        최고 관리자 계정은 여기서 바꿀 수 없습니다.
      </p>
      <form
        className="admin-form"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied(query.trim());
          void load(query.trim());
        }}
      >
        <label>
          아이디·표시 이름 검색
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={64}
          />
        </label>
        <button type="submit" className="mapping-run">
          검색
        </button>
      </form>
      <div className="admin-filters" role="group" aria-label="계정 상태">
        {(["all", "pending", "active", "rejected", "suspended"] as const).map(
          (s) => (
            <button
              key={s}
              type="button"
              className={`admin-filter${filter === s ? " is-active" : ""}`}
              aria-pressed={filter === s}
              onClick={() => setFilter(s)}
            >
              {s === "all" ? "전체" : ACCOUNT_STATUS_LABEL[s]} {counts[s] ?? 0}
            </button>
          ),
        )}
      </div>
      {notice && (
        <p className="mapping-message" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {pending && (
        <section
          className="admin-panel"
          aria-label="작업 확인"
          aria-live="polite"
        >
          {pending.kind === "status" ? (
            <>
              <h3>{ACTION[pending.action].label}</h3>
              <p>
                대상: <strong>{pending.user.username}</strong> (
                {pending.user.display_name}) · 현재 상태{" "}
                <StatusBadge status={pending.user.status} /> → 결과{" "}
                <StatusBadge status={ACTION[pending.action].result} />
              </p>
              <label>
                {ACTION[pending.action].reasonHint}
                <textarea
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  maxLength={500}
                  rows={2}
                />
              </label>
            </>
          ) : (
            <>
              <h3>비밀번호 초기화</h3>
              <p>
                대상: <strong>{pending.user.username}</strong> (
                {pending.user.display_name}) · 기존 비밀번호는 볼 수 없으며,
                초기화하면 그 사용자의 로그인이 모두 종료됩니다. 새 비밀번호는
                안전한 방법으로 직접 전달하세요.
              </p>
              <div className="admin-form">
                <label>
                  새 비밀번호 (8자 이상)
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    minLength={8}
                    maxLength={128}
                  />
                </label>
                <label>
                  새 비밀번호 확인
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={newPassword2}
                    onChange={(e) => setNewPassword2(e.target.value)}
                    maxLength={128}
                  />
                </label>
              </div>
            </>
          )}
          <div className="admin-actions">
            <button
              type="button"
              className="mapping-run"
              disabled={
                busy || (pending.kind === "password" && newPassword.length < 8)
              }
              onClick={() => void confirm()}
            >
              {busy
                ? "처리 중…"
                : `'${pending.user.username}' ${pending.kind === "status" ? ACTION[pending.action].label : "비밀번호 초기화"} 확정`}
            </button>
            <button
              type="button"
              className="mapping-confirm"
              disabled={busy}
              onClick={() => setPending(null)}
            >
              취소
            </button>
          </div>
        </section>
      )}
      {users && shown.length === 0 && (
        <p className="upload-drop-hint">조건에 맞는 계정이 없습니다.</p>
      )}
      {shown.length > 0 && (
        <div className="upload-table-wrap">
          <table className="upload-table runs-table admin-users-table">
            <thead>
              <tr>
                <th scope="col">아이디</th>
                <th scope="col">표시 이름</th>
                <th scope="col">역할</th>
                <th scope="col">상태</th>
                <th scope="col">가입일</th>
                <th scope="col">최근 로그인</th>
                <th scope="col">최근 업무 활동</th>
                <th scope="col">관리</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((u) => (
                <tr key={u.id}>
                  <td data-label="아이디">
                    <strong>{u.username}</strong>
                  </td>
                  <td data-label="표시 이름">{u.display_name}</td>
                  <td data-label="역할">
                    {u.role === "owner" ? "최고 관리자" : "일반 사용자"}
                  </td>
                  <td data-label="상태">
                    <span>
                      <StatusBadge status={u.status} />
                      {u.status_reason && (
                        <span className="dash-sub admin-reason">
                          사유: {u.status_reason}
                        </span>
                      )}
                    </span>
                  </td>
                  <td data-label="가입일">{minute(u.created_at)}</td>
                  <td data-label="최근 로그인">{minute(u.last_login_at)}</td>
                  <td data-label="최근 업무 활동">
                    {minute(u.last_activity_at)}
                  </td>
                  <td data-label="관리" className="admin-row-actions">
                    {u.role === "owner" ? (
                      <span className="dash-sub">최고 관리자 — 변경 불가</span>
                    ) : (
                      <span className="admin-buttons">
                        {ACTIONS_FOR[u.status].map((a) => (
                          <button
                            key={a}
                            type="button"
                            className={`mapping-confirm${a === "reject" || a === "suspend" ? " wk-danger" : ""}`}
                            aria-label={`${u.username} ${ACTION[a].label}`}
                            disabled={busy}
                            onClick={() =>
                              open({ kind: "status", user: u, action: a })
                            }
                          >
                            {ACTION[a].label}
                          </button>
                        ))}
                        {(u.status === "active" ||
                          u.status === "suspended") && (
                          <button
                            type="button"
                            className="mapping-confirm"
                            aria-label={`${u.username} 비밀번호 초기화`}
                            disabled={busy}
                            onClick={() => open({ kind: "password", user: u })}
                          >
                            비밀번호 초기화
                          </button>
                        )}
                        {u.status === "rejected" && (
                          <span className="dash-sub">처리 완료(반려)</span>
                        )}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <AddUser
        onAdded={(text) => {
          setNotice(text);
          void load(applied);
        }}
      />
    </section>
  );
}

function StatusBadge({ status }: { status: AccountStatus }) {
  return (
    <span className={`admin-badge ${STATUS_BADGE[status] ?? ""}`}>
      {ACCOUNT_STATUS_LABEL[status] ?? status}
    </span>
  );
}

function AddUser({ onAdded }: { onAdded: (message: string) => void }) {
  const [form, setForm] = useState({
    username: "",
    display_name: "",
    password: "",
  });
  const [status, setStatus] = useState<"active" | "pending" | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!status || busy) return;
    setBusy(true);
    setError(null);
    try {
      const made = await createUser({ ...form, status });
      onAdded(
        `'${made.username}' 계정을 추가했습니다 — 일반 사용자 · ${made.status_label}`,
      );
      setForm({ username: "", display_name: "", password: "" });
      setStatus("");
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="admin-help">
      <summary>계정 직접 추가 (일반 사용자)</summary>
      <form className="admin-form" onSubmit={(e) => void submit(e)}>
        <label>
          아이디
          <input
            value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })}
            required
            maxLength={32}
            placeholder="영문 소문자·숫자"
          />
        </label>
        <label>
          표시 이름
          <input
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            required
            maxLength={64}
          />
        </label>
        <label>
          처음 비밀번호
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })}
            required
            minLength={8}
            maxLength={128}
          />
        </label>
        <fieldset className="admin-choice">
          <legend>승인 여부 (반드시 선택)</legend>
          <label>
            <input
              type="radio"
              name="add-status"
              checked={status === "active"}
              onChange={() => setStatus("active")}
            />
            바로 이용 (승인함)
          </label>
          <label>
            <input
              type="radio"
              name="add-status"
              checked={status === "pending"}
              onChange={() => setStatus("pending")}
            />
            승인 대기로 추가
          </label>
        </fieldset>
        <button
          type="submit"
          className="mapping-run"
          disabled={busy || !status}
        >
          {busy ? "추가 중…" : "계정 추가"}
        </button>
      </form>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
    </details>
  );
}

// ---------------------------------------------------------------- 관리 이력

function Audit() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    fetchAudit()
      .then(setEvents)
      .catch((err: unknown) => setError(message(err)));
  }, []);

  const q = query.trim().toLowerCase();
  const shown = (events ?? []).filter(
    (e) =>
      !q ||
      e.target_name.toLowerCase().includes(q) ||
      (e.actor_name ?? "").toLowerCase().includes(q),
  );

  return (
    <section className="admin admin-wide" aria-label="관리 이력">
      <h2>관리 이력</h2>
      <p className="upload-drop-hint">
        가입 신청·승인·반려·이용 중지·재개·비밀번호 초기화·계정 추가와 역할
        전환을 최신 순으로 최대 200건 보여줍니다. '반영 안 됨'은 상태가 맞지
        않아(중복 클릭·이미 처리된 신청 등) 계정을 바꾸지 않은 요청입니다.
        비밀번호는 기록하지 않습니다.
      </p>
      <div className="admin-form">
        <label>
          아이디로 거르기 (대상·작업자)
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            maxLength={32}
          />
        </label>
      </div>
      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}
      {events && shown.length === 0 && (
        <p className="upload-drop-hint">기록이 없습니다.</p>
      )}
      {shown.length > 0 && (
        <div className="upload-table-wrap">
          <table className="upload-table runs-table admin-audit-table">
            <thead>
              <tr>
                <th scope="col">시각</th>
                <th scope="col">작업자</th>
                <th scope="col">대상</th>
                <th scope="col">작업</th>
                <th scope="col">변경</th>
                <th scope="col">사유</th>
                <th scope="col">결과</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((e) => (
                <tr key={e.id}>
                  <td data-label="시각">{formatTime(e.created_at)}</td>
                  <td data-label="작업자">{e.actor_name ?? "(시스템 전환)"}</td>
                  <td data-label="대상">{e.target_name}</td>
                  <td data-label="작업">{e.action_label}</td>
                  <td data-label="변경">
                    {e.before_label || e.after_label
                      ? `${e.before_label ?? "(없음)"} → ${e.after_label ?? "-"}`
                      : "-"}
                    {e.role_change && (
                      <span className="dash-sub admin-reason">
                        역할: {e.role_change}
                      </span>
                    )}
                  </td>
                  <td data-label="사유">{e.reason ?? "-"}</td>
                  <td data-label="결과">
                    <span
                      className={`admin-badge ${e.result === "ok" ? "is-approved" : "is-rejected"}`}
                    >
                      {e.result_label}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default MoajwoAdmin;
