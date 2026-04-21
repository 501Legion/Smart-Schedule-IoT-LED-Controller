🚀 Smart Schedule & IoT LED Controller
Google Calendar API와 Raspberry Pi를 결합한 지능형 일정 관리 및 하드웨어 제어 시스템

본 프로젝트는 사용자의 Google Calendar 일정과 연동하여 하루의 업무 흐름을 LED 스트립으로 시각화하고, 주요 일정에 대해 음성(TTS) 및 시각적 알림을 제공하는 IoT 솔루션입니다. 추가적으로 Flask 웹 서버를 통해 원격으로 하드웨어를 제어할 수 있는 인터페이스를 제공합니다.

🛠 Tech Stack
Language: Python 3.x

Hardware: Raspberry Pi, WS281x LED Strip, Speaker

Libraries:

Hardware Control: RPi.GPIO, rpi_ws281x

Web Framework: Flask

API: Google Calendar API

Voice: gTTS, espeak-ng

Concepts: Multithreading, RESTful API, Singleton Pattern, Signal Processing (Sine Wave)

✨ Key Features
1. 실시간 일정 동기화 및 시각화 (Main System)
Progress Bar Visualization: Google Calendar API를 통해 당일 업무 시간을 파악하고, LED 스트립을 통해 현재 업무 진행도를 실시간으로 표시합니다.

Multithreaded Update: 메인 애니메이션 루프와 별개로 백그라운드 스레드에서 일정 데이터를 주기적으로 갱신하여 끊김 없는 사용자 경험을 제공합니다.

Dynamic Animations:

Work Start: 업무 시작 시 부드러운 웨이브(Wave) 효과로 시스템 활성화를 알립니다.

Hometime/Finish: 모든 일정 종료 시 무지개(Rainbow) 애니메이션을 통해 보상감을 제공합니다.

2. 지능형 알림 시스템 (Smart Alert)
Dual-Mode TTS: 환경에 따라 고음질 gTTS(온라인) 또는 빠른 응답의 espeak-ng(오프라인) 모드를 선택하여 일정 알림을 송출합니다. (예: "XX 회의 5분 전입니다.")

Visual Alarm: 중요한 일정 시작 시 LED 스트립 전체가 특정 색상으로 점멸하여 시각적 주의를 환기합니다.

3. 원격 제어 및 확장성 (Remote Control)
Flask REST API: 웹 브라우저나 외부 HTTP 요청을 통해 LED의 On/Off 및 하드웨어 상태를 원격으로 제어할 수 있습니다.

Hardware Abstraction: main.py와 test.py를 통해 시스템 통합 운영과 개별 컴포넌트 테스트 환경을 분리했습니다.

🏗 System Architecture
Data Layer: Google Calendar API로부터 JSON 데이터를 수신 및 파싱.

Control Layer:

Calendar Thread: 일정 데이터 관리 및 동기화.

Main Loop: 시간 기반 LED 렌더링 알고리즘 수행.

Singleton Lock: /tmp/iot_main.lock 파일을 통한 프로세스 중복 실행 방지.

Output Layer: PWM 제어를 통한 LED 스트립 구동 및 ALSA 오디오 장치를 통한 음성 출력.

💻 Installation & Setup
1. 의존성 설치
Bash
sudo apt-get update
sudo apt-get install python3-pip mpg123 espeak-ng
pip3 install rpi_ws281x requests flask gtts
2. Google Calendar API 설정
Google Cloud Console에서 API 키와 Calendar ID를 발급받아 config.py에 설정해야 합니다.

3. 실행
Bash
# 메인 시스템 실행
sudo python3 main.py

# 웹 제어 서버 실행 (별도 터미널)
python3 test.py
🔍 Technical Highlights
Mathematical Animation: Sine 함수를 이용한 밝기 변조 및 색상 보간법을 적용하여 자연스러운 시각 효과 구현.

Concurrency Management: threading.Lock을 사용하여 음성 출력 장치(ALSA)의 공유 자원 충돌 문제 해결.

Robustness: 프로세스 비정상 종료 시에도 finally 구문을 통해 GPIO를 안전하게 초기화(Cleanup)하도록 설계.

📂 Project Structure
main.py: 실시간 일정 연동 및 LED/TTS 메인 로직.

test.py: Flask 기반 원격 하드웨어 제어 API 서버.

config.py: API 키, 핀 번호, 색상 테마 등 사용자 설정 파일.

utils/: 시간 계산 및 문자열 포맷팅 유틸리티.
