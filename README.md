# TDS Quiz Solver Agent

This repository contains an autonomous quiz-solving agent built for the TDS-2 Data Tasks evaluation.  
The system receives quiz tasks through a POST API, loads JavaScript-rendered quiz pages, extracts instructions, downloads referenced files, performs data processing or analysis, and submits correct answers to the evaluation server.  
The solver supports multi-step chained quizzes and completes each full quiz sequence within the required three-minute window.

---

## Features

- Flask API endpoint that accepts quiz requests.
- Secret-key validation for secure access.
- Background thread execution to immediately return HTTP 200 while solving continues.
- Playwright integration to load JS-rendered quiz pages.
- Automatic extraction of instructions, submit URLs, data links, and embedded content.
- File handling for:
  - CSV
  - JSON
  - PDF (text and tables)
  - XLSX / Excel
- Optional audio transcription using the OpenAI API.
- LLM-based reasoning for complex or ambiguous instructions.
- Autonomous multi-step URL chaining until the quiz ends.
- Execution time limited to 170 seconds to meet evaluation constraints.
- Robust error handling and fallback logic.

---

## Project Structure

tds-quiz-agent/
│
├── app.py # Flask server and API entrypoint
├── solver.py # Multi-step quiz solver engine
├── utils.py # File download and parsing utilities
├── llm_agent.py # Optional LLM reasoning module
├── requirements.txt
├── .gitignore
└── README.md

yaml
Copy code

---

## Installation

### 1. Clone the repository

git clone https://github.com/Meghanaiitm/tds-quiz-agent
cd tds-quiz-agent

shell
Copy code

### 2. Install dependencies

pip install -r requirements.txt
python -m playwright install

pgsql
Copy code

### 3. Create `.env` (not committed to Git)

QUIZ_SECRET=your_secret_here
OPENAI_API_KEY=your_openai_key # optional
FLASK_ENV=development
PORT=3000

yaml
Copy code

---

## Running the API

Start the Flask server:

python app.py

yaml
Copy code

The service runs at:

http://127.0.0.1:3000

yaml
Copy code

---

## Testing the Solver

From PowerShell:

Invoke-WebRequest -Uri "http://localhost:3000/api/quiz" -Method POST
-Headers @{ "Content-Type" = "application/json" } `
-Body '{"email":"me@me.com","secret":"your_secret_here","url":"https://tds-llm-analysis.s-anand.net/demo"}'

yaml
Copy code

Expected immediate API response:

{"status": "accepted"}

yaml
Copy code

The solver will continue running in the background, following quiz URLs and submitting answers.

---

## Deployment

The project can be deployed on Render, Railway, or any Python-compatible cloud platform.

### Render Deployment Summary

- Connect this GitHub repository to Render.
- Use the following commands:

**Build command**
pip install -r requirements.txt && python -m playwright install

bash
Copy code

**Start command**
gunicorn app:APP --timeout 200

yaml
Copy code

- Add environment variables:
  - QUIZ_SECRET
  - OPENAI_API_KEY
  - PORT

### Railway Deployment Summary

- Connect the GitHub repository.
- Add the same build and start commands.
- Configure the same environment variables.

---

## License

This project is licensed under the MIT License.  
See the `LICENSE` file for details.

---

## Author

Gadi Meghana  
TDS-2 Autonomous Quiz Solver Project
