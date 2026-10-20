# Trivy 재집계 (원시 결과 기준, 새 스캔 없음)

```
Version: 0.76.0
Vulnerability DB:
  Version: 2
  UpdatedAt: 2026-10-19 19:08:34.442427859 +0000 UTC
  NextUpdate: 2026-10-20 19:08:34.442427458 +0000 UTC
  DownloadedAt: 2026-10-19 21:30:07.258189936 +0000 UTC
```

## 이미지별 검출

| 이미지 | 검사 대상(Trivy ImageID = 설정 다이제스트) | OS | 검출 행 (심각도별) | 중복 뺀 ID | HIGH 행 | HIGH 중복 뺀 ID | HIGH 수정 상태 |
|---|---|---|---|---|---|---|---|
| backend | `sha256:e6aeab741489f7f46c09de76b578a1b60609e3fefaf0e5cf0ee339abd47ce8ba` | debian 13.7 | {'LOW': 62, 'HIGH': 46, 'MEDIUM': 64, 'UNKNOWN': 3} 합 175 | 82 | 46 | 10 | {'affected': 43, 'fix_deferred': 1, 'fixed': 2} |
| frontend | `sha256:686922855c46b7134645ac68fae12ece48f9f74b3fa7253de0275ac01ef39a95` | alpine 3.23.4 | {'HIGH': 45, 'MEDIUM': 84, 'LOW': 50, 'UNKNOWN': 2} 합 181 | 113 | 45 | 33 | {'fixed': 45} |

- 두 이미지에 함께 있는 ID(전 심각도): 4개 — CVE-2026-76642, CVE-2026-78408, CVE-2026-78410, CVE-2026-85091
- 두 이미지 모두에서 HIGH인 ID: 3개 — CVE-2026-76642, CVE-2026-78408, CVE-2026-78410
- 두 이미지의 HIGH 중복 뺀 ID 합집합: 40개 (행 수 합과 다름)

## backend HIGH 이상 전체 행 (46행)

| 패키지 | 설치 버전 | 취약점 ID | 수정 버전 | 수정 상태 | 심각도 출처 | 자료 출처 | 레이어 | 대상 |
|---|---|---|---|---|---|---|---|---|
| bsdutils | 1:2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| bsdutils | 1:2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| bsdutils | 1:2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| bsdutils | 1:2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| jaraco.context | 5.3.0 | CVE-2026-23949 | 6.1.0 | fixed | ghsa | ghsa:HIGH, redhat:HIGH, ubuntu:MEDIUM | GitHub Security Advisory pip | 3/9 | python-pkg |
| libacl1 | 2.3.2-2+b1 | CVE-2026-54369 | 없음 | affected | (배포판 기본) | alma:HIGH, amazon:HIGH, azure:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH | Debian Security Tracker | 1/9 | debian |
| libblkid1 | 2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libblkid1 | 2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libblkid1 | 2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libblkid1 | 2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| liblastlog2-2 | 2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| liblastlog2-2 | 2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| liblastlog2-2 | 2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| liblastlog2-2 | 2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libmount1 | 2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libmount1 | 2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libmount1 | 2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libmount1 | 2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libncursesw6 | 6.5+20250216-2 | CVE-2025-69720 | 없음 | affected | nvd | alma:MEDIUM, azure:CRITICAL, cbl-mariner:CRITICAL, julia:HIGH, nvd:HIGH, oracle-oval:MEDIUM, photon:HIGH, redhat:MEDIUM, rocky:MEDIUM, ubuntu:LOW | Debian Security Tracker | 3/9 | debian |
| libsmartcols1 | 2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libsmartcols1 | 2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libsmartcols1 | 2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libsmartcols1 | 2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libsystemd0 | 257.13-1~deb13u1 | CVE-2026-16742 | 없음 | affected | (배포판 기본) | amazon:HIGH, azure:MEDIUM, redhat:HIGH, ubuntu:MEDIUM | Debian Security Tracker | 1/9 | debian |
| libtinfo6 | 6.5+20250216-2 | CVE-2025-69720 | 없음 | affected | nvd | alma:MEDIUM, azure:CRITICAL, cbl-mariner:CRITICAL, julia:HIGH, nvd:HIGH, oracle-oval:MEDIUM, photon:HIGH, redhat:MEDIUM, rocky:MEDIUM, ubuntu:LOW | Debian Security Tracker | 1/9 | debian |
| libudev1 | 257.13-1~deb13u1 | CVE-2026-16742 | 없음 | affected | (배포판 기본) | amazon:HIGH, azure:MEDIUM, redhat:HIGH, ubuntu:MEDIUM | Debian Security Tracker | 1/9 | debian |
| libuuid1 | 2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libuuid1 | 2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libuuid1 | 2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| libuuid1 | 2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| login | 1:4.16.0-2+really2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| login | 1:4.16.0-2+really2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| login | 1:4.16.0-2+really2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| login | 1:4.16.0-2+really2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| mount | 2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| mount | 2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| mount | 2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| mount | 2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| ncurses-base | 6.5+20250216-2 | CVE-2025-69720 | 없음 | affected | nvd | alma:MEDIUM, azure:CRITICAL, cbl-mariner:CRITICAL, julia:HIGH, nvd:HIGH, oracle-oval:MEDIUM, photon:HIGH, redhat:MEDIUM, rocky:MEDIUM, ubuntu:LOW | Debian Security Tracker | 1/9 | debian |
| ncurses-bin | 6.5+20250216-2 | CVE-2025-69720 | 없음 | affected | nvd | alma:MEDIUM, azure:CRITICAL, cbl-mariner:CRITICAL, julia:HIGH, nvd:HIGH, oracle-oval:MEDIUM, photon:HIGH, redhat:MEDIUM, rocky:MEDIUM, ubuntu:LOW | Debian Security Tracker | 1/9 | debian |
| perl-base | 5.40.1-6+deb13u1 | CVE-2026-9538 | 없음 | fix_deferred | nvd | alma:HIGH, amazon:HIGH, nvd:HIGH, oracle-oval:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Debian Security Tracker | 1/9 | debian |
| util-linux | 2.41.5-0+deb13u1 | CVE-2026-76642 | 없음 | affected | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| util-linux | 2.41.5-0+deb13u1 | CVE-2026-78408 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| util-linux | 2.41.5-0+deb13u1 | CVE-2026-78409 | 없음 | affected | (배포판 기본) | redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| util-linux | 2.41.5-0+deb13u1 | CVE-2026-78410 | 없음 | affected | (배포판 기본) | azure:HIGH, redhat:HIGH | Debian Security Tracker | 1/9 | debian |
| wheel | 0.45.1 | CVE-2026-24049 | 0.46.2 | fixed | ghsa | alma:HIGH, amazon:HIGH, azure:MEDIUM, cbl-mariner:MEDIUM, ghsa:HIGH, nvd:MEDIUM, oracle-oval:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | GitHub Security Advisory pip | 3/9 | python-pkg |

## frontend HIGH 이상 전체 행 (45행)

| 패키지 | 설치 버전 | 취약점 ID | 수정 버전 | 수정 상태 | 심각도 출처 | 자료 출처 | 레이어 | 대상 |
|---|---|---|---|---|---|---|---|---|
| c-ares | 1.34.6-r0 | CVE-2026-33630 | 1.34.8-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, azure:HIGH, oracle-oval:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-11352 | 8.22.0-r0 | fixed | (배포판 기본) | julia:HIGH, redhat:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-11586 | 8.22.0-r0 | fixed | (배포판 기본) | amazon:MEDIUM, julia:HIGH, photon:HIGH, redhat:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-12064 | 8.22.0-r0 | fixed | (배포판 기본) | amazon:MEDIUM, azure:HIGH, julia:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-5773 | 8.20.0-r0 | fixed | nvd | julia:HIGH, nvd:HIGH, photon:HIGH, redhat:MEDIUM, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-6276 | 8.20.0-r0 | fixed | nvd | azure:MEDIUM, julia:HIGH, nvd:HIGH, photon:HIGH, redhat:LOW, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-8286 | 8.22.0-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:MEDIUM, azure:HIGH, julia:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-8458 | 8.22.0-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:MEDIUM, azure:MEDIUM, julia:MEDIUM, oracle-oval:HIGH, photon:MEDIUM, redhat:HIGH, rocky:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-8925 | 8.22.0-r0 | fixed | (배포판 기본) | amazon:MEDIUM, julia:CRITICAL, photon:CRITICAL, redhat:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-8927 | 8.22.0-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:MEDIUM, azure:MEDIUM, julia:CRITICAL, oracle-oval:HIGH, photon:CRITICAL, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| curl | 8.17.0-r1 | CVE-2026-9547 | 8.22.0-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:MEDIUM, julia:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libcrypto3 | 3.5.6-r0 | CVE-2026-14456 | 3.5.8-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 1/10 | alpine |
| libcrypto3 | 3.5.6-r0 | CVE-2026-45447 | 3.5.7-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, azure:HIGH, julia:HIGH, oracle-oval:MEDIUM, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:HIGH | Alpine Secdb | 1/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-11352 | 8.22.0-r0 | fixed | (배포판 기본) | julia:HIGH, redhat:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-11586 | 8.22.0-r0 | fixed | (배포판 기본) | amazon:MEDIUM, julia:HIGH, photon:HIGH, redhat:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-12064 | 8.22.0-r0 | fixed | (배포판 기본) | amazon:MEDIUM, azure:HIGH, julia:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-5773 | 8.20.0-r0 | fixed | nvd | julia:HIGH, nvd:HIGH, photon:HIGH, redhat:MEDIUM, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-6276 | 8.20.0-r0 | fixed | nvd | azure:MEDIUM, julia:HIGH, nvd:HIGH, photon:HIGH, redhat:LOW, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-8286 | 8.22.0-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:MEDIUM, azure:HIGH, julia:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-8458 | 8.22.0-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:MEDIUM, azure:MEDIUM, julia:MEDIUM, oracle-oval:HIGH, photon:MEDIUM, redhat:HIGH, rocky:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-8925 | 8.22.0-r0 | fixed | (배포판 기본) | amazon:MEDIUM, julia:CRITICAL, photon:CRITICAL, redhat:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-8927 | 8.22.0-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:MEDIUM, azure:MEDIUM, julia:CRITICAL, oracle-oval:HIGH, photon:CRITICAL, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libcurl | 8.17.0-r1 | CVE-2026-9547 | 8.22.0-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:MEDIUM, julia:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:LOW | Alpine Secdb | 8/10 | alpine |
| libexpat | 2.7.5-r0 | CVE-2026-45186 | 2.8.1-r0 | fixed | nvd | alma:HIGH, amazon:HIGH, azure:LOW, julia:HIGH, nvd:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libexpat | 2.7.5-r0 | CVE-2026-66046 | 2.8.4-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, azure:HIGH, oracle-oval:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libexpat | 2.7.5-r0 | CVE-2026-76641 | 2.8.4-r0 | fixed | (배포판 기본) | azure:HIGH, redhat:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libexpat | 2.7.5-r0 | CVE-2026-76956 | 2.8.4-r0 | fixed | nvd | azure:MEDIUM, nvd:HIGH, redhat:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libexpat | 2.7.5-r0 | CVE-2026-76957 | 2.8.4-r0 | fixed | nvd | amazon:HIGH, azure:MEDIUM, nvd:HIGH, redhat:MEDIUM, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libexpat | 2.7.5-r0 | CVE-2026-77214 | 2.9.0-r0 | fixed | (배포판 기본) | azure:HIGH, redhat:HIGH | Alpine Secdb | 8/10 | alpine |
| libexpat | 2.7.5-r0 | CVE-2026-93990 | 2.8.5-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, azure:HIGH, oracle-oval:HIGH, redhat:HIGH, rocky:HIGH | Alpine Secdb | 8/10 | alpine |
| libssl3 | 3.5.6-r0 | CVE-2026-14456 | 3.5.8-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 1/10 | alpine |
| libssl3 | 3.5.6-r0 | CVE-2026-45447 | 3.5.7-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, azure:HIGH, julia:HIGH, oracle-oval:MEDIUM, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:HIGH | Alpine Secdb | 1/10 | alpine |
| libuuid | 2.41.4-r0 | CVE-2026-53612 | 2.41.6-r0 | fixed | (배포판 기본) | redhat:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libuuid | 2.41.4-r0 | CVE-2026-53613 | 2.41.6-r0 | fixed | (배포판 기본) | redhat:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libuuid | 2.41.4-r0 | CVE-2026-53614 | 2.41.6-r0 | fixed | (배포판 기본) | redhat:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| libuuid | 2.41.4-r0 | CVE-2026-76642 | 2.41.6-r0 | fixed | (배포판 기본) | azure:MEDIUM, photon:HIGH, redhat:HIGH | Alpine Secdb | 8/10 | alpine |
| libuuid | 2.41.4-r0 | CVE-2026-78408 | 2.41.6-r1 | fixed | (배포판 기본) | azure:HIGH, redhat:HIGH | Alpine Secdb | 8/10 | alpine |
| libuuid | 2.41.4-r0 | CVE-2026-78410 | 2.41.6-r0 | fixed | (배포판 기본) | azure:HIGH, redhat:HIGH | Alpine Secdb | 8/10 | alpine |
| libxml2 | 2.13.9-r0 | CVE-2026-6732 | 2.13.9-r1 | fixed | nvd | julia:HIGH, nvd:HIGH, photon:HIGH, redhat:MEDIUM, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| nghttp2-libs | 1.68.0-r0 | CVE-2026-27135 | 1.68.1 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, azure:HIGH, cbl-mariner:HIGH, julia:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 8/10 | alpine |
| pcre2 | 10.47-r0 | CVE-2026-103111 | 10.49-r0 | fixed | (배포판 기본) | alma:HIGH, azure:HIGH, oracle-oval:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 2/10 | alpine |
| pcre2 | 10.47-r0 | CVE-2026-86145 | 10.48-r0 | fixed | (배포판 기본) | alma:HIGH, azure:HIGH, oracle-oval:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 2/10 | alpine |
| pcre2 | 10.47-r0 | CVE-2026-89157 | 10.48-r0 | fixed | nvd | alma:HIGH, azure:MEDIUM, nvd:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:MEDIUM, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 2/10 | alpine |
| pcre2 | 10.47-r0 | CVE-2026-89161 | 10.48-r0 | fixed | nvd | alma:HIGH, amazon:HIGH, nvd:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH, ubuntu:MEDIUM | Alpine Secdb | 2/10 | alpine |
| tiff | 4.7.1-r0 | CVE-2026-4775 | 4.7.2-r0 | fixed | (배포판 기본) | alma:HIGH, amazon:HIGH, azure:HIGH, oracle-oval:HIGH, photon:HIGH, redhat:HIGH, rocky:HIGH | Alpine Secdb | 8/10 | alpine |

