# sn2_backup

AMR(`app_slamnav2`)이 생성하는 `~/data/sn2_log/` 데이터를 자동으로 개인 Google Drive에 업로드하고,
업로드 실패·신규 에러 같은 이벤트만 이메일로 알려주는 도구.

- **트리거**: systemd timer로 15분마다 실행 (oneshot). `Persistent=true`라 로봇이 꺼져 있던 동안 놓친 실행은 부팅 후 자동 보충.
- **안전장치**: `mtime`이 5분 이상 지난 파일만 후보 — 쓰여지는 중인 파일은 건드리지 않음.
- **멱등**: 이미 올린 파일(같은 size + mtime)은 재업로드 안 함. 상태는 `~/.sn2_backup/state.json`.
- **읽기 전용**: 로컬 원본은 절대 수정·삭제하지 않음. 데이터 관리는 `app_slamnav2` 책임.
- **Drive 구조**: `<parent>/<robot_id>/<원본_상대경로>` 미러링.

## 사전 요구사항

### 노트북 (한 번)
- Python 3.10+, `python3-venv`
- 본인 Google 계정 + Chrome 등 브라우저 (OAuth 동의용)

### 로봇 (각 로봇마다)
- Ubuntu/Debian + systemd
- 인터넷 접속 가능
- `python3-venv` 패키지는 install 스크립트가 **없으면 자동으로 apt install**해 — 별도 사전 작업 불필요. (sudo 프롬프트만 한 번 더 뜸.) 수동으로 미리 깔고 싶으면 Ubuntu 22.04 기준 `sudo apt install -y python3.10-venv`.

## 1회 셋업

두 가지 경로 중 골라:

- **🚀 한 줄 배포** (개인용 권장): 노트북에서 자격증명·앱비번을 한 번에 묶어 self-install 스크립트로 만들고, 로봇은 그걸 한 줄로 실행. → [한 줄 배포](#-한-줄-배포-self-install-bundle)
- **단계별 셋업**: 자격증명을 SCP로 보내고 로봇에서 install 스크립트가 인터랙티브로 앱 비번을 받음. → [단계별 셋업](#단계별-셋업)

### 🚀 한 줄 배포 (self-install bundle)

#### ① ~ ③ Google 쪽 준비 (둘 다 동일)

[① OAuth 클라이언트](#-노트북-google-drive-oauth-클라이언트) →
[② token.json 발급](#-노트북-토큰-발급) →
[③ 앱 비밀번호 발급](#-google-gmail-앱-비밀번호-발급) 까지는 어느 경로든 똑같음.

#### ④ (노트북) 번들 생성 후 로봇에 한 번에 보내기

```bash
cd ~/ws/data_manager
bash scripts/build_bundle.sh \
    --credentials ~/Downloads/credentials.json \
    --token       ./token.json \
    --app-password "xxxx xxxx xxxx xxxx" \
    > sn2_install.sh

# 빌드 결과 확인 (정상이면 ~40KB; 0바이트면 인자 잘못된 거니 stderr 확인)
ls -la sn2_install.sh

# 로봇에 SCP 후 직접 실행 (sudo 비번 입력해야 하므로 SSH로 들어가서)
scp sn2_install.sh rainbow@<robot>:
ssh rainbow@<robot> 'bash sn2_install.sh'
```

> ⚠️ `ssh ... 'bash -s' < sn2_install.sh` 형태도 가능은 하지만, 스크립트가 sudo로 systemd unit을 깔 때 stdin이 파일로 묶여 있어서 비번 입력을 못 받음. **SCP 후 SSH 인터랙티브 실행**을 권장.

번들 안에 들어가는 것:

- 리포지토리 전체(.git 제외, ~30KB tar.gz)
- `credentials.json`, `token.json` (base64로 임베드)
- 앱 비밀번호 (env 파일에 평문)

번들 크기는 ~40KB 수준이라 어디든 보낼 수 있어. 동일한 번들로 여러 로봇에 똑같이 깔 수도 있음(robot_id는 자동 결정되니 충돌 없음).

⚠️ 보안: 번들 파일은 **자격증명을 평문으로 담고 있음**. 개인 사용 외엔 비추.

이걸로 끝. 로봇에서 sudo 비번 한 번 입력하면 systemd timer까지 enable되고 첫 사이클이 시작 시점부터 돌기 시작해.

---

### 단계별 셋업

```
[노트북]                              [로봇]
  ① OAuth 클라이언트 발급
  ② token.json 발급
  ③ Gmail 앱 비번 발급
  ④ 2개 파일 SCP   ─────────────▶  ⑤ install_on_robot.sh 실행
                                       (앱 비번 인터랙티브 입력 → 자동 셋업)
```

<a id="-노트북-google-drive-oauth-클라이언트"></a>
### ① (노트북) Google Drive OAuth 클라이언트

1. [Google Cloud Console](https://console.cloud.google.com) → 프로젝트 생성 (이미 있으면 그대로)
2. **API 및 서비스 → 라이브러리 → "Google Drive API" 검색 → 사용 설정**
3. **API 및 서비스 → OAuth 동의 화면**
   - **External** 선택 → 만들기
   - 앱 이름 아무거나, 사용자 지원 이메일/개발자 연락처는 본인 Gmail
   - **저장 후 계속** → Scopes 그냥 **저장 후 계속**
   - **테스트 사용자** 섹션에 본인 Gmail을 반드시 추가 ⚠️
   - **저장 후 계속**
4. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID**
   - 애플리케이션 유형: **데스크톱 앱**
   - 이름: 아무거나
   - **만들기** 누르면 모달에 비밀번호가 표시됨 — **그 자리에서 JSON 다운로드** 클릭
   - ⚠️ 모달을 닫고 나면 비밀번호는 영영 못 가져옴 (Google 정책). 만약 닫아버렸으면, 클라이언트 상세 페이지에서 **"+ Add secret"** 버튼으로 새 비밀번호 추가하면 됨.
5. 받은 JSON을 `~/Downloads/credentials.json`로 저장

> ⚠️ **테스트 사용자 등록 잊으면 OAuth 동의 시 `403 access_denied`** 발생. 그 페이지에서 "고급 → 앱(안전하지 않음)으로 이동"으로 우회되지 않으니 반드시 ③단계에서 등록할 것.

<a id="-노트북-토큰-발급"></a>
### ② (노트북) 토큰 발급

```bash
git clone <this-repo>.git ~/ws/data_manager && cd ~/ws/data_manager
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python scripts/authorize_drive.py ~/Downloads/credentials.json ./token.json
```

브라우저가 자동으로 열리고:
1. 본인 Google 계정 선택
2. **"Google이 이 앱을 확인하지 않았습니다"** 경고 → 좌하단 **고급** → **앱(안전하지 않음)으로 이동** 클릭
3. **계속 → 허용**
4. 터미널에 `wrote ./token.json` + 브라우저에 "The authentication flow has completed" 표시되면 성공

<a id="-google-gmail-앱-비밀번호-발급"></a>
### ③ (Google) Gmail 앱 비밀번호 발급

1. 본인 Google 계정에 2단계 인증 활성화
2. https://myaccount.google.com/apppasswords 에서 앱 비밀번호 발급
3. 16자리 문자열을 보관

### ④ (노트북 → 로봇) 자격증명 2개 SCP

앱 비밀번호는 ⑤단계 스크립트가 인터랙티브로 받아주니 SCP로 보낼 필요 없음. credentials/token만 보내면 됨.

```bash
ssh rainbow@<robot> 'mkdir -p ~/.sn2_backup && chmod 700 ~/.sn2_backup'
scp credentials.json token.json rainbow@<robot>:~/.sn2_backup/
```

### ⑤ (로봇) 설치 스크립트 한 방

```bash
ssh rainbow@<robot>
git clone <this-repo>.git ~/ws/data_manager
cd ~/ws/data_manager
bash scripts/install_on_robot.sh
```

스크립트가 하는 일:

- 의존성 점검 (`systemctl`, `sudo`) + `python3 venv/ensurepip` 가용성 확인 — 없으면 `python<X.Y>-venv`를 **apt로 자동 설치** (sudo 프롬프트)
- `credentials.json`, `token.json` 존재 확인 — 없으면 안내 후 중단
- `env` 파일 없으면 **앱 비밀번호를 입력 받아서 자동 생성** (입력 가려짐, 16자 검증, `chmod 600`)
- `~/.sn2_backup/venv` 생성 + `pip install -e <repo>` — 이전에 `python3-venv` 누락으로 깨진 venv가 있으면 **자동 감지하고 재생성**
- `~/.sn2_backup/config.yaml` 없으면 템플릿에서 생성 (있으면 손대지 않음)
- `--dry-run` 스모크 테스트 1회 실행 (Drive에 안 올림)
- systemd unit 두 개 설치 후 `enable --now`
- 다음 실행 시각 / 상태 출력

스크립트는 **재실행 안전**(idempotent). 셋업 검증만 하고 싶으면:

```bash
bash scripts/install_on_robot.sh --check
```

systemd 없이 dry-run까지만 보고 싶으면 (예: 노트북 테스트):

```bash
bash scripts/install_on_robot.sh --no-systemd
```

## 업데이트 (이미 깐 로봇에 최신 코드 적용)

GitHub `main` 브랜치에서 최신 코드를 받아와 재설치하는 한 줄 스크립트:

```bash
bash ~/ws/data_manager/scripts/update_on_robot.sh
```

스크립트가 하는 일:

- **보존**: `~/.sn2_backup/{credentials.json, token.json, env, config.yaml, state.json}` (자격증명·앱비번·robot_id·업로드 이력 모두 그대로)
- **교체**: `~/ws/data_manager/` (기존은 `.bak.<timestamp>`로 백업 후 fresh clone)
- **재생성**: `~/.sn2_backup/venv/` (새 의존성 반영을 위해 강제 재생성)
- 끝나면 `install_on_robot.sh`를 자동 호출 → systemd unit 재등록 + dry-run smoke test

업데이트 중에도 기존 systemd timer는 계속 돌아. 사이클이 도중에 겹치면 systemd가 중복 실행을 막아주니 안전.

포크 사용 중이면 환경변수로 override:

```bash
SN2_REPO_URL=https://github.com/me/data_manager.git \
SN2_REPO_BRANCH=develop \
bash ~/ws/data_manager/scripts/update_on_robot.sh
```

> ⚠️ **첫 설치엔 이 스크립트 사용 불가**. 자격증명·토큰·env가 없는 로봇이면 안내 메시지 출력 후 종료해. 첫 설치는 [한 줄 배포](#-한-줄-배포-self-install-bundle) 사용.

## 동작 확인

```bash
# 다음 실행 예정 시각
systemctl list-timers | grep sn2-backup

# 최근 사이클 로그 스트림
journalctl -u sn2-backup -f

# 한 번 즉시 실행 (수동)
sudo systemctl start sn2-backup.service

# 노트북/로봇 어디서든, 업로드 안 하고 후보 파일만 확인
~/.sn2_backup/venv/bin/python -m sn2_backup \
  --config ~/.sn2_backup/config.yaml --dry-run -v
```

## 개발 / 테스트

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

테스트는 외부 API(Drive·SMTP)를 모두 fake로 대체하므로 네트워크 없이 돌아간다.

## 디렉터리 레이아웃

```
data_manager/
├── pyproject.toml
├── config.example.yaml
├── README.md
├── systemd/
│   ├── sn2-backup.service
│   └── sn2-backup.timer
├── scripts/
│   ├── build_bundle.sh       # 노트북에서 self-install bundle 생성 (한 줄 배포용)
│   ├── install_on_robot.sh   # 로봇에서 한 방에 설치 + systemd 등록
│   ├── update_on_robot.sh    # 로봇에서 최신 코드 GitHub에서 받아와 재설치 (자격증명 보존)
│   └── authorize_drive.py    # 노트북에서 1회 OAuth → token.json 발급
├── src/sn2_backup/
│   ├── __init__.py
│   ├── __main__.py        # CLI 진입점
│   ├── config.py          # YAML 로드/검증
│   ├── identity.py        # robot_id 결정 (config → dmidecode → /sys/class/net 기반 MAC → hostname)
│   ├── state.py           # state.json 원자 읽기/쓰기
│   ├── scanner.py         # data_root walk, mtime 가드, dedup
│   ├── error_watch.py     # error_logs/ 신규 에러 라인 추출
│   ├── drive.py           # DriveClient 프로토콜 + GoogleDriveClient + 헬퍼
│   ├── email_notify.py    # SMTP Notifier + 이벤트 본문 빌더
│   └── runner.py          # run_once 오케스트레이션
└── tests/                 # pytest, fakes only — 네트워크 없음
```

## 보안 / 권한

- `credentials.json`, `token.json`, `~/.sn2_backup/env`, `~/.sn2_backup/state.json` 은 모두 `chmod 600`.
- `.gitignore`에 위 파일들 포함 — 절대 커밋되지 않음. self-install 번들(`sn2_install.sh`)도 평문 자격증명을 담으므로 `.gitignore`에 포함됨.
- OAuth scope는 `drive.file` 하나(이 앱이 만든/연 파일만 접근). Drive 전체 접근 안 함.

## Troubleshooting

### `403 access_denied` (OAuth 동의 화면)
원인: OAuth 동의 화면의 **테스트 사용자** 목록에 본인 Gmail이 등록 안 됨.
해결: https://console.cloud.google.com/auth/audience → **테스트 사용자** → **+ 사용자 추가**로 본인 이메일 등록 → `authorize_drive.py` 재실행.

### "JSON 다운로드" 버튼이 안 보임 (OAuth 클라이언트)
Google이 정책을 바꿔서 클라이언트 비밀번호는 **만들 때 1회만** 표시·다운로드 가능. 모달 닫혔으면 영구 마스킹됨(`****Uilo` 형태).
해결: 클라이언트 상세 페이지의 **"+ Add secret"** 버튼으로 새 비밀번호 추가 → 그때 표시되는 모달에서 JSON 다운로드.

### `ensurepip is not available` / venv 생성 실패
원인: 로봇에 `python3-venv` 패키지 미설치 (Ubuntu 22.04에선 `python3.10-venv`).
해결: 최신 install 스크립트는 이 패키지를 **apt로 자동 설치**해 주니까 그냥 다시 돌리면 됨:
```bash
rm -rf ~/.sn2_backup/venv      # 깨진 venv 정리 (install 스크립트도 자동으로 처리하지만 명시적이 안전)
bash ~/ws/data_manager/scripts/install_on_robot.sh
```
구버전 번들로 설치 중이면 한 번 수동으로:
```bash
sudo apt install -y python3.10-venv
```

### `sn2_install.sh`가 0바이트로 만들어짐
원인: `build_bundle.sh`가 인자 검증에서 실패했고, 에러는 stderr로 흘렀는데 stdout 리다이렉트로 빈 파일만 생성됨.
해결: stderr를 따로 잡아 확인.
```bash
bash scripts/build_bundle.sh --credentials A --token B --app-password "C" \
    > sn2_install.sh 2>build_err.log
cat build_err.log    # 어떤 인자가 빠졌는지/잘못됐는지 보임
```

### SSH로 `bash -s < sn2_install.sh` 했더니 출력 없이 끝남
원인: 스크립트 내부의 `sudo`가 비번을 받아야 하는데 stdin이 파일에 묶여 있어 prompt가 동작하지 않음.
해결: SCP 후 SSH로 들어가서 직접 실행:
```bash
scp sn2_install.sh rainbow@<robot>:
ssh rainbow@<robot> 'bash sn2_install.sh'
```

### 오늘 날짜의 daily log (`snlog_YYYY-MM-DD.log`)가 Drive에 안 올라감
원인: 기본 `mtime_quiet_seconds: 300` 가드 — 5분 동안 mtime 변경 없는 파일만 후보. 로봇이 동작 중이면 오늘 daily log는 계속 append되어 mtime이 항상 fresh → 가드에 걸려 그날 안에 안 올라감. 어제 분은 자정 rotate 후 더 안 변하니 다음 사이클에 정상 업로드됨.

해결: `~/.sn2_backup/config.yaml`의 `scanner.always_upload_globs`에 패턴 추가. 매칭되는 파일은 mtime 가드 무시하고 (size, mtime_ns) 변경분만 dedup해서 매 사이클마다 업로드.
```yaml
scanner:
  mtime_quiet_seconds: 300
  always_upload_globs:
    - "snlog/snlog_*.log"
    - "snlog/*-log-list.html"
```
적용:
```bash
sudo systemctl start sn2-backup.service
journalctl -u sn2-backup -n 30 --no-pager
```
`config.example.yaml`엔 이 두 패턴이 기본으로 들어 있어. 새로 깔린 로봇이라면 자동 적용됨. 기존 config는 직접 추가해야 해.

⚠️ 트래픽 트레이드오프: 매 사이클(15분)마다 daily log 전체를 Drive update로 올림. 15MB log면 8시간 동작 시 ~480MB/day 업로드. 개인 Drive 쿼터 안에서 무시 가능한 수준이지만 기억해둬.

### 첫 사이클인데 메일이 안 옴 / Drive에 폴더가 안 생김
체크리스트:
1. `journalctl -u sn2-backup -n 100 --no-pager` — 에러 메시지 확인
2. `cat ~/.sn2_backup/state.json` — `last_cycle.failed` 값과 `uploaded` 개수 확인
3. 수동으로 한 번 즉시 트리거: `sudo systemctl start sn2-backup.service`
4. 메일이 스팸함에 들어갔는지 확인 (Gmail SMTP는 자기 발송이라도 첫 회 스팸 분류 가능)
5. Drive 부모 폴더 ID가 맞는지: `grep parent_folder_id ~/.sn2_backup/config.yaml`

### 같은 hostname 로봇이 여러 대인데 robot_id가 충돌
원인: 자동 폴백 체인이 `dmidecode → MAC → hostname` 순인데, dmidecode가 의미없는 값(`Default String` 등)을 내고 MAC 후보가 docker/veth 인터페이스로 잡혀 걸러지면 hostname까지 내려와서 같은 ID로 폴딩됨.

해결 (둘 중 하나):
1. **명시적 robot_id**(권장 — fleet 배포): 각 로봇 `~/.sn2_backup/config.yaml`에 `robot_id: S100-B-001` 처럼 박고 `rm ~/.sn2_backup/state.json && sudo systemctl start sn2-backup.service`. 변경되면 새 ID로 자동 재-announcement 메일이 발송됨.
2. **자동 폴백에 의존**: 현재 구현은 `/sys/class/net/*/address`를 직접 읽고 docker/veth/br-* 등을 제외하므로 같은 모델이라도 NIC 공장 MAC 끝 6자리가 달라 자동 분리됨. 진단:
   ```bash
   ~/.sn2_backup/venv/bin/python -c "
   from sn2_backup.identity import _real_get_mac, select_primary_mac
   import glob, os
   addrs = []
   for p in sorted(glob.glob('/sys/class/net/*/address')):
       iface = os.path.basename(os.path.dirname(p))
       mac = open(p).read().strip()
       addrs.append((iface, mac))
   print('all:', addrs)
   print('selected:', select_primary_mac(addrs))
   "
   ```

### 어느 robot_id가 어느 물리 로봇인지 헷갈림
첫 사이클 announcement 메일에 `robot_id` + `hostname` + 모든 비-가상 인터페이스의 `(이름, MAC, IPv4)`가 포함돼서 매핑할 수 있어. 메일 잃었으면 로봇에서 직접:
```bash
~/.sn2_backup/venv/bin/python -c "
from sn2_backup.identity import gather_host_info, resolve_robot_id
import yaml
cfg = yaml.safe_load(open('/home/rainbow/.sn2_backup/config.yaml'))
print('robot_id =', resolve_robot_id(config_value=cfg.get('robot_id')))
print(gather_host_info())
"
```
