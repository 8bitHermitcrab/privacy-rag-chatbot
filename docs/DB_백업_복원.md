# DB 백업과 복원

## 백업 만들기

- **수동**: 저장소 폴더의 `DB_백업.cmd`를 더블클릭합니다. 명령으로는 `python -m starter.db_backup`입니다.
- **확인**: `DB_백업.cmd verify`를 실행하면 가장 최근 백업을 임시 DB에 실제로 복원해 표별 행 수를 운영 DB와 비교하고, 임시 DB는 지웁니다. 운영 DB는 읽기만 합니다.
- **목록**: `DB_백업.cmd list`

| 항목 | 내용 |
| --- | --- |
| 저장 위치 | `%LOCALAPPDATA%\RagServerAdmin\db_backups` (서버 관리자와 같은 비공개 폴더, 본인·SYSTEM만 접근) |
| 파일 이름 | `pii_rag_YYYYMMDD_HHMMSS.dump` (pg_dump custom 형식, 압축) |
| 보관 개수 | 최근 14개 (`.env`의 `DB_BACKUP_KEEP`으로 변경) |
| 포함 | 모든 스키마·데이터·권한(GRANT)·데이터 버전 스키마(`rag_v2` 등)·버전 등록부 |
| 미포함 | DB 계정 비밀번호 (`.env`와 시스템 시작이 관리) |

**백업 파일에는 원래 개인정보(vault)와 계정 정보가 들어 있습니다.** 저장소(Git) 폴더, 공유 폴더, 메신저로 옮기지 마세요. 다른 위치에 두려면 `.env`의 `DB_BACKUP_DIR`에 절대 경로를 적고, 그 폴더의 접근 권한은 직접 관리합니다.

## 매일 자동 백업 (Windows 작업 스케줄러)

관리자 권한이 아닌 일반 명령 프롬프트에서 한 번 실행합니다. 경로는 본인 PC에 맞게 바꿉니다. `create` 인자를 주면 창이 멈추지 않고 끝납니다.

```
schtasks /Create /TN "RAG DB 백업" /SC DAILY /ST 18:30 /TR "\"C:\Private\AI\02. RAG\Git\DB_백업.cmd\" create"
```

Docker Desktop이 켜져 있어야 백업됩니다. 꺼져 있던 날은 백업되지 않습니다.

## 클라우드 (서버 3 → 서버 2 복사)

서버 구성(`RAG_3서버_인프라_구축안`)에서 DB는 서버 3의 Docker(`pii_rag_db`)에 있습니다. 백업은 **서버 3에서 실행**하고, 서버 3이 망가져도 남도록 **서버 2로 복사**합니다. 서비스 중에 실행해도 되며 챗봇 1·2는 영향을 받지 않습니다. 단, 마이그레이션이나 데이터 버전 `create`/`drop`처럼 표 구조를 바꾸는 작업과는 시간을 겹치지 않습니다.

**1. 서버 2: 보관 계정과 폴더 (한 번)**
```
sudo adduser --disabled-password --gecos "" backup_rag
sudo mkdir -p /var/backups/pii_rag && sudo chown backup_rag: /var/backups/pii_rag && sudo chmod 700 /var/backups/pii_rag
```

**2. 백업 전용 SSH 키 등록 (한 번)**

   서버 3에서 키를 만들고 공개키 내용을 확인합니다.
```
ssh-keygen -t ed25519 -N "" -f ~/.ssh/rag_backup        # 이미 있으면 생략
cat ~/.ssh/rag_backup.pub
```
   서버 2에서 위 공개키 한 줄을 `backup_rag` 계정에 등록합니다. 앞의 `restrict`는 이 키로 터미널·포트 전달을 못 하게 막는 옵션입니다(rsync는 동작).
```
sudo -u backup_rag mkdir -p -m 700 /home/backup_rag/.ssh
echo 'restrict 여기에_공개키_한_줄' | sudo -u backup_rag tee -a /home/backup_rag/.ssh/authorized_keys
sudo chmod 600 /home/backup_rag/.ssh/authorized_keys
```
   서버 3의 `~/.ssh/config`에 다음을 넣어 이 키를 쓰게 합니다.
```
Host 10.0.0.12
    User backup_rag
    IdentityFile ~/.ssh/rag_backup
```
   백업을 실행하는 계정은 `docker` 그룹이어야 합니다(`sudo usermod -aG docker 계정`, 다시 로그인).

**3. 서버 3: `.env`에 복사 위치 추가**
```
DB_BACKUP_COPY_TO=backup_rag@10.0.0.12:/var/backups/pii_rag
```

**4. 서버 3: 손으로 한 번 실행해 확인**
```
bash tools/db_backup.sh; tail -20 ~/.local/share/rag-server-admin/db_backup.log
.venv/bin/python -m starter.db_backup verify
ssh backup_rag@10.0.0.12 ls -l /var/backups/pii_rag
```

**5. 서버 3: 매일 자동 실행 (cron)**
```
crontab -e
# 매일 03:17 (저장소 경로는 실제 위치로)
17 3 * * * bash /home/계정/privacy-rag-chatbot/tools/db_backup.sh
```

- 서버 3의 백업은 `~/.local/share/rag-server-admin/db_backups`(권한 700)에, 서버 2의 복사본은 `/var/backups/pii_rag`(파일 600)에 있습니다. 두 곳 모두 최근 14개만 유지합니다.
- 복사에 실패해도 서버 3의 백업은 남고, 기록에 "복사에 실패했습니다"와 종료코드 1이 남습니다. 기록 파일을 주기적으로 확인하세요.
- 서버 3이 망가졌을 때는 새 서버 3에 DB를 만든 뒤(`시스템 시작`과 같은 준비), 서버 2의 가장 최근 파일을 가져와 아래 '복원' 절차를 따릅니다.

## 언제 꼭 백업하나

- 키 교체(`docs/키_교체_절차.md`)의 6번 전환 전, 9번 `drop` 전
- 재적재(`ingest --reset`)와 인물사전 재생성 전
- 팀원이 공유한 INSERT 쿼리로 실제 데이터를 넣기 전과 후

## 복원 (운영 DB를 백업 시점으로 되돌리기)

운영 DB를 덮어쓰는 작업이라 프로그램 명령으로 만들지 않았습니다. 아래 순서를 직접 따릅니다.

1. 서버 관리자에서 챗봇 1, 2와 분산기를 중지합니다.
2. 지금 상태도 한 번 백업합니다(`DB_백업.cmd`). 되돌리기가 잘못됐을 때를 대비합니다.
3. 백업 파일이 있는 폴더에서 명령 프롬프트를 열고 실행합니다(파일 이름은 바꿔서).
   ```
   docker exec -i pii_rag_db dropdb -U admin pii_rag
   docker exec -i pii_rag_db createdb -U admin pii_rag
   docker exec -i pii_rag_db pg_restore -U admin -d pii_rag --exit-on-error < pii_rag_20261125_120000.dump
   ```
4. 시스템을 시작해 챗봇 1, 2와 분산기를 켭니다. 서버가 시작할 때 DB 버전과 키 짝을 다시 확인합니다.

새 PC나 새 DB에 복원할 때는 먼저 `시스템_시작.cmd`로 DB와 계정(rag_user 등)을 만든 뒤 3번을 실행합니다.
