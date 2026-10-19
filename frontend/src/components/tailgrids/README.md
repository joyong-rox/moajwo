# Tailgrids 컴포넌트 (MIT)

`npx @tailgrids/cli@1.4.3 add button alert-dialog` · `add toggle radio-input`으로 받은 Tailgrids 컴포넌트입니다
(라이선스: 같은 폴더의 LICENSE.md).

- 원본: https://github.com/TailGrids/tailgrids — `apps/docs/src/registry/core/` (커밋 b54636bac9fa6ab64cf5eff388c3de07b6adc515, 2026-08-04)
- 받은 파일: `core/button.tsx`, `core/alert-dialog.tsx`, `core/overlay.tsx` (alert-dialog가 쓰는 배경),
  `core/toggle.tsx`(통계 요약 켜기), `core/radio-input.tsx`(결과 형태 고르기) — 뒤의 둘은 원본 그대로
  - 함께 받아진 `core/dialog.tsx`는 쓰지 않아 뺐습니다 (없는 `./description`과 `@tailgrids/icons`를 요구함).
- 원본에서 바꾼 곳: 이 저장소의 TypeScript 설정(`verbatimModuleSyntax`)에 맞춰 타입 가져오기 4곳을 `import type`/`type`으로만 고쳤습니다.
  동작은 같습니다.
- 테마 변수·초기화 범위는 `src/tailgrids.css` 머리말을 보세요.
