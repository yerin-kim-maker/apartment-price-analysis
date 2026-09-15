@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  아파트 실거래가 분석 대시보드 실행 중...
echo  (이 창을 닫으면 서버가 꺼집니다)
echo ============================================
python -m streamlit run app\Home.py
