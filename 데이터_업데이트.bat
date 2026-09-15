@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  아파트 실거래가 데이터 업데이트
echo ============================================
echo.
if not exist ".env" (
  echo [안내] .env 파일이 없습니다.
  echo        .env.example 파일을 복사해 .env로 이름을 바꾸고
  echo        DATA_GO_KR_KEY 값을 채운 뒤 다시 실행하세요.
  echo.
  pause
  exit /b 1
)
echo 필요한 패키지를 설치합니다...
python -m pip install -q -r requirements.txt
echo.
echo 서울+경기 전체 아파트 매매/전월세 실거래가를 수집합니다.
echo (처음 실행하면 데이터 양이 많아 시간이 오래 걸릴 수 있습니다. 창을 닫지 마세요.)
echo.
python scripts\fetch_transactions.py
echo.
echo ============================================
echo  완료. 대시보드_실행.bat 를 실행해서 확인하세요.
echo ============================================
pause
