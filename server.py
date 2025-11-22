#!/usr/bin/env python3

import json
import sqlite3
import hashlib
from wsgiref.simple_server import make_server
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, parse_qs
from io import BytesIO
import ssl
import os
from datetime import datetime

ssl._create_default_https_context = ssl._create_unverified_context

ADMIN_PASSWORD = "admin123"
ADMIN_USERNAME = "admin"

AI_API_KEY = "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"
AI_API_URL = "https://openrouter.ai/api/v1/chat/completions"

def init_db():
    conn = sqlite3.connect('reports.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            type TEXT NOT NULL,
            language TEXT DEFAULT 'kazakh',
            word_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    
    cursor.execute("SELECT id FROM users WHERE username = ?", (ADMIN_USERNAME,))
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)",
            (ADMIN_USERNAME, hash_password(ADMIN_PASSWORD), "admin@localhost")
        )
        print("✅ Администратор құрылды: admin / admin123")
    
    conn.commit()
    conn.close()
    print("✅ Дерекқор инициализацияланды")

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, password_hash):
    return hash_password(password) == password_hash

def is_admin(user):
    return user == ADMIN_USERNAME

def register_user(username, password, email=""):
    conn = sqlite3.connect('reports.db', check_same_thread=False)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?)",
            (username, hash_password(password), email)
        )
        conn.commit()
        user_id = cursor.lastrowid
        conn.close()
        return {"success": True, "user_id": user_id}
    except sqlite3.IntegrityError:
        conn.close()
        return {"success": False, "error": "Бұл пайдаланушы аты бар қолданушы бұрыннан бар"}
    except Exception as e:
        conn.close()
        return {"success": False, "error": str(e)}

def login_user(username, password):
    conn = sqlite3.connect('reports.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash FROM users WHERE username = ?", (username,))
    user = cursor.fetchone()
    conn.close()
    
    if user and verify_password(password, user[2]):
        return {"success": True, "user_id": user[0], "username": user[1]}
    else:
        return {"success": False, "error": "Пайдаланушы аты немесе пароль дұрыс емес"}

def save_report_to_db(user_id, title, content, content_type, language="kazakh", word_count=0):
    conn = sqlite3.connect('reports.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO reports (user_id, title, content, type, language, word_count) VALUES (?, ?, ?, ?, ?, ?)',
        (user_id, title, content, content_type, language, word_count)
    )
    conn.commit()
    report_id = cursor.lastrowid
    conn.close()
    return report_id

def get_user_reports(user_id):
    conn = sqlite3.connect('reports.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, title, content, type, language, word_count, created_at FROM reports WHERE user_id = ? ORDER BY created_at DESC',
        (user_id,)
    )
    reports = cursor.fetchall()
    conn.close()
    return reports

def get_all_users():
    conn = sqlite3.connect('reports.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, email, created_at FROM users ORDER BY created_at DESC')
    users = cursor.fetchall()
    conn.close()
    return users

def get_all_reports():
    conn = sqlite3.connect('reports.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT r.id, u.username, r.title, r.content, r.type, r.language, r.word_count, r.created_at 
        FROM reports r 
        JOIN users u ON r.user_id = u.id 
        ORDER BY r.created_at DESC
    ''')
    reports = cursor.fetchall()
    conn.close()
    return reports

def get_db_stats():
    conn = sqlite3.connect('reports.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    user_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM reports')
    report_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM reports WHERE type = "presentation"')
    presentation_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM reports WHERE date(created_at) = date("now")')
    today_reports = cursor.fetchone()[0]
    cursor.execute('SELECT username FROM users ORDER BY created_at DESC LIMIT 1')
    last_user = cursor.fetchone()
    conn.close()
    return {
        'user_count': user_count,
        'report_count': report_count,
        'presentation_count': presentation_count,
        'today_reports': today_reports,
        'last_user': last_user[0] if last_user else 'Пайдаланушы жоқ'
    }

def call_openrouter_api(prompt, content_type, language="kazakh", word_count=500):
    try:
        language_names = {
            'kazakh': 'kk',
            'russian': 'ru',
            'english': 'en'
        }
        lang_code = language_names.get(language, 'kk')
        
        if content_type == "presentation":
            system_message = f"""Сіз презентацияларды жасау бойынша сарапшысыз. {lang_code} тілінде құрылымдық презентация жасаңыз.
            
Формат: әр слайдтың тақырыбы және 3-5 негізгі тармақтары болуы керек.
Құрылым: кіріспе, негізгі бөлімдер, қорытынды.
Көлем: шамамен {word_count} сөз.
Стиль: кәсіби, ақпараттық, нақты фактілермен."""
            
            user_message = f'Презентация тақырыбы: "{prompt}". Тілі: {lang_code}. Көлемі: ~{word_count} сөз. Тақырыптары мен тармақтары бар анық слайд құрылымын жасаңыз.'
        else:
            system_message = f"""Сіз академиялық авторсыз. Қатаң түрде {lang_code} тілінде, анық және құрылымды түрде жазыңыз.
            
Құрылым: кіріспе, 2-4 бөлім, қорытынды.
Артық сөздерден аулақ болыңыз. Ішкі тақырыптарын қолданыңыз.
Мақсатты көлем - шамамен {word_count} сөз."""
            
            user_message = f'Реферат тақырыбы: "{prompt}". Тілі: {lang_code}. Көлемі: ~{word_count} сөз. Егер тақырып кең болса - назарды тарылтып, кіріспеде қысқаша мазмұн ұсыныңыз.'

        data = {
            "model": "openai/gpt-3.5-turbo",
            "messages": [
                {
                    "role": "system",
                    "content": system_message
                },
                {
                    "role": "user", 
                    "content": user_message
                }
            ],
            "max_tokens": 4000,
            "temperature": 0.7
        }

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {AI_API_KEY}',
            'HTTP-Referer': 'http://localhost:8000',
            'X-Title': 'AI Report Generator'
        }

        req = urlrequest.Request(
            AI_API_URL,
            data=json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )

        with urlrequest.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode('utf-8'))
            
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0]['message']['content']
                print(f"✅ OpenRouter API арқылы {content_type} сәтті генерацияланды")
                return content
            else:
                print("❌ API жауабында қате")
                return generate_fallback_content(prompt, content_type, language, word_count)
                
    except Exception as e:
        print(f"❌ OpenRouter API қатесі: {e}")
        return generate_fallback_content(prompt, content_type, language, word_count)

def generate_fallback_content(prompt, content_type, language="kazakh", word_count=500):
    
    templates = {
        'kazakh': {
            'presentation': f"""ПРЕЗЕНТАЦИЯ: {prompt.upper()}

1-СЛАЙД: КІРІСПЕ {prompt.upper()}
• Тақырыптың өзектілігі мен маңыздылығы
• Қарастырылатын негізгі сұрақтар
• Презентацияның мақсаттары мен міндеттері

2-СЛАЙД: НЕГІЗГІ ТҮСІНІКТЕР
• Негізгі анықтамалар мен терминдер
• Зерттеудің теориялық негізі
• Әдіснамалық тәсіл

3-СЛАЙД: ТАЛДАУ ЖӘНЕ ЗЕРТТЕУЛЕР
• Мәселенің қазіргі жағдайы
• Жүргізілген зерттеулер
• Алынған деректер мен статистика

4-СЛАЙД: ПРАКТИКАЛЫҚ ҚОЛДАНЫЛУЫ
• Қолдану мысалдары
• Кейстер және сәтті тәжірибелер
• Қолдану бойынша ұсыныстар

5-СЛАЙД: ДАМУ ПЕРСПЕКТИВАЛАРЫ
• Үрдістер мен болжамдар
• Әрі қарай зерттеулерге мүмкіндіктер
• Мамандарға арналған ұсыныстар

6-СЛАЙД: ҚОРЫТЫНДЫ
• Негізгі қорытындылар
• Негізгі ұсыныстар
• Назарларыңызға рахмет""",

            'referat': f"""РЕФЕРАТ ТАҚЫРЫБЫ: "{prompt.upper()}"

КІРІСПЕ

"{prompt}" тақырыбын зерттеудің өзектілігі оның қазіргі әлемдегі маңыздылығымен байланысты. Бұл жұмыс осы мәселенің негізгі аспектілерін кешенді зерттеуге, қолданыстағы тәсілдерді талдауға және қорытындыларды қалыптастыруға бағытталған.

НЕГІЗГІ БӨЛІМ

1. {prompt} тақырыбының теориялық аспектілері

Мәселені зерттеу тарихы бірнеше онжылдықты қамтиды. Осы уақыт ішінде осы тақырыпты зерттеудің әртүрлі ғылыми мектептері мен тәсілдері қалыптасты.

Негізгі теориялық ережелерге заманауи зерттеулердің негізі болған бірқатар маңызды тұжырымдамалар мен әдіснамалық принциптер кіреді.

2. Практикалық маңызы және қолданылуы

{prompt} туралы білімдерді практикалық қолдану әртүрлі салаларда жоғары тиімділікті көрсетеді. Зерттеу нәтижелері ғылымда, білім беруде және өнеркәсіпте қолданылады.

Көптеген case-зерттеулер алынған деректердің құндылығын және олардың өзекті мәселелерді шешудегі практикалық маңыздылығын растайды.

ҚОРЫТЫНДЫ

Жүргізілген зерттеу "{prompt}" тақырыбының даму әлеуетінің айтарлықтай екені туралы қорытынды жасауға мүмкіндік берді. Алынған нәтижелер теориялық да, практикалық та құндылыққа ие.

Осы бағыттағы әрі қарай зерттеулер жаңа ашылуларға және практикалық қолдануға әкелуі мүмкін.

ПАЙДАЛАНЫЛҒАН ӘДЕБИЕТТЕР ТІЗІМІ

1. "{prompt}" тақырыбы бойынша заманауи зерттеулер
2. Рецензияланатын журналдардағы ғылыми жарияланымдар
3. Халықаралық конференция материалдары
4. Статистикалық деректер мен есептер"""
        },
        'russian': {
            'presentation': f"""ПРЕЗЕНТАЦИЯ: {prompt.upper()}

СЛАЙД 1: ВВЕДЕНИЕ В {prompt.upper()}
• Актуальность и значимость темы
• Основные вопросы для рассмотрения
• Цели и задачи презентации

СЛАЙД 2: ОСНОВНЫЕ ПОНЯЯТИЯ
• Ключевые определения и термины
• Теоретическая база исследования
• Методологический подход

СЛАЙД 3: АНАЛИЗ И ИССЛЕДОВАНИЯ
• Современное состояние вопроса
• Проведенные исследования
• Полученные данные и статистика

СЛАЙД 4: ПРАКТИЧЕСКОЕ ПРИМЕНЕНИЕ
• Примеры использования
• Кейсы и успешные практики
• Рекомендации по применению

СЛАЙД 5: ПЕРСПЕКТИВЫ РАЗВИТИЯ
• Тенденции и прогнозы
• Возможности для дальнейших исследований
• Рекомендации для специалистов

СЛАЙД 6: ЗАКЛЮЧЕНИЕ
• Основные выводы
• Ключевые рекомендации
• Благодарность за внимание""",

            'referat': f"""РЕФЕРАТ НА ТЕМУ: "{prompt.upper()}"

ВВЕДЕНИЕ

Актуальность исследования темы "{prompt}" обусловлена ее значимостью в современном мире. Данная работа направлена на комплексное изучение основных аспектов данной проблематики, анализ существующих подходов и формулирование выводов.

ОСНОВНАЯ ЧАСТЬ

1. Теоретические аспекты {prompt}

История изучения вопроса насчитывает несколько десятилетий. За это время сформировались различные научные школы и подходы к исследованию данной темы. 

Основные теоретические положения включают в себя ряд важных концепций и методологических принципов, которые легли в основу современных исследований.

2. Практическое значение и применение

Практическое применение знаний о {prompt} демонстрирует высокую эффективность в различных сферах. Результаты исследований находят применение в науке, образовании и промышленности.

Многочисленные case-исследования подтверждают ценность полученных данных и их практическую значимость для решения актуальных задач.

3. Современное состояние и перспективы

На современном этапе наблюдается активное развитие исследований в области {prompt}. Новые технологии и методики позволяют получать более точные и релевантные данные.

Перспективы дальнейших исследований связаны с интеграцией междисциплинарных подходов и применением современных технологий анализа.

ЗАКЛЮЧЕНИЕ

Проведенное исследование позволило сделать вывод о значительном потенциале развития темы "{prompt}". Полученные результаты имеют как теоретическую, так и практическую ценность.

Дальнейшие исследования в данном направлении могут привести к новым открытиям и практическим применениям.

СПИСОК ИСПОЛЬЗОВАННОЙ ЛИТЕРАТУРЫ

1. Современные исследования по теме "{prompt}"
2. Научные публикации в рецензируемых журналах
3. Материалы международных конференций
4. Статистические данные и отчеты"""
        },
        'english': {
            'presentation': f"""PRESENTATION: {prompt.upper()}

SLIDE 1: INTRODUCTION TO {prompt.upper()}
• Relevance and significance of the topic
• Key questions to consider
• Goals and objectives

SLIDE 2: KEY CONCEPTS
• Main definitions and terminology
• Theoretical framework
• Methodological approach

SLIDE 3: ANALYSIS AND RESEARCH
• Current state of research
• Conducted studies
• Obtained data and statistics

SLIDE 4: PRACTICAL APPLICATION
• Usage examples
• Case studies and best practices
• Implementation recommendations

SLIDE 5: DEVELOPMENT PROSPECTS
• Trends and forecasts
• Opportunities for further research
• Recommendations for specialists

SLIDE 6: CONCLUSION
• Main conclusions
• Key recommendations
• Thank you for attention""",
            
            'referat': f"""ESSAY ON: "{prompt.upper()}"

INTRODUCTION

The relevance of researching the topic "{prompt}" is determined by its significance in the modern world. This work aims to comprehensively study the main aspects of this problem, analyze existing approaches and formulate conclusions.

MAIN CONTENT

1. Theoretical aspects of {prompt}

The history of studying this issue spans several decades. During this time, various scientific schools and approaches to researching this topic have been formed.

The main theoretical provisions include a number of important concepts and methodological principles that formed the basis of modern research.

2. Practical significance and application

The practical application of knowledge about {prompt} demonstrates high efficiency in various fields. Research results are used in science, education and industry.

Numerous case studies confirm the value of the obtained data and their practical significance for solving current problems.

CONCLUSION

The conducted research allowed us to conclude about the significant development potential of the topic "{prompt}". The obtained results have both theoretical and practical value.

Further research in this direction may lead to new discoveries and practical applications."""
        }
    }
    
    lang_templates = templates.get(language, templates['kazakh'])
    content = lang_templates.get(content_type, lang_templates['referat'])
    
    print(f"✅ Резервті генерация қолданылды ({language}, {word_count} сөз)")
    return content

def call_openrouter(api_key, prompt, content_type, language="kazakh", word_count=500):
    print(f"🔮 {content_type} генерациясы: {prompt} (тілі: {language}, сөз: {word_count})")
    return call_openrouter_api(prompt, content_type, language, word_count)

def make_docx(text: str, title: str = "Реферат") -> bytes:
    content = f"{title}\n\n{text}"
    return content.encode('utf-8')

def make_presentation_docx(text: str, title: str = "Презентация") -> bytes:
    content = f"Презентация: {title}\n\n{text}"
    return content.encode('utf-8')

def make_presentation_pdf(text: str, title: str = "Презентация") -> bytes:
    content = f"Презентация: {title}\n\n{text}"
    return content.encode('utf-8')

def make_formatted_txt(text: str) -> bytes:
    return text.encode('utf-8')

def make_presentation_html(text: str, title: str) -> bytes:
    
    slides = parse_presentation_to_slides(text)
    
    html_content = f"""<!DOCTYPE html>
<html lang="kk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Презентация: {title}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
            color: #f8fafc;
            overflow: hidden;
            height: 100vh;
        }}
        
        .presentation-container {{
            width: 100vw;
            height: 100vh;
            position: relative;
            display: flex;
            flex-direction: column;
        }}
        
        .header {{
            background: rgba(15, 23, 42, 0.95);
            padding: 15px 30px;
            border-bottom: 2px solid rgba(99, 102, 241, 0.3);
            display: flex;
            justify-content: space-between;
            align-items: center;
            z-index: 100;
        }}
        
        .header h1 {{
            font-size: 1.5rem;
            font-weight: 600;
            background: linear-gradient(135deg, #6366f1 0%, #10b981 50%, #f59e0b 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        
        .slide-counter {{
            font-size: 1rem;
            color: #64748b;
            font-weight: 500;
        }}
        
        .slides-container {{
            flex: 1;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 40px;
            position: relative;
        }}
        
        .slide {{
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.95) 0%, rgba(15, 23, 42, 0.95) 100%);
            border-radius: 20px;
            padding: 50px;
            width: 90vw;
            height: 70vh;
            border: 2px solid rgba(99, 102, 241, 0.2);
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            display: none;
            animation: slideIn 0.5s ease-out;
            position: relative;
            overflow-y: auto;
        }}
        
        .slide.active {{
            display: block;
        }}
        
        .slide-title {{
            text-align: center;
            font-size: 2.5rem;
            font-weight: 700;
            margin-bottom: 40px;
            background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            padding-bottom: 15px;
            border-bottom: 2px solid rgba(99, 102, 241, 0.3);
        }}
        
        .slide-content {{
            font-size: 1.4rem;
            line-height: 1.8;
            color: #e2e8f0;
        }}
        
        .slide-content ul {{
            list-style: none;
            padding-left: 20px;
        }}
        
        .slide-content li {{
            margin-bottom: 20px;
            padding-left: 30px;
            position: relative;
        }}
        
        .slide-content li:before {{
            content: "•";
            color: #6366f1;
            font-size: 2rem;
            position: absolute;
            left: 0;
            top: -5px;
        }}
        
        .controls {{
            position: fixed;
            bottom: 30px;
            left: 50%;
            transform: translateX(-50%);
            display: flex;
            gap: 15px;
            background: rgba(15, 23, 42, 0.9);
            padding: 15px 25px;
            border-radius: 50px;
            border: 2px solid rgba(99, 102, 241, 0.3);
            backdrop-filter: blur(10px);
        }}
        
        .control-btn {{
            background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
            color: white;
            border: none;
            padding: 12px 20px;
            border-radius: 25px;
            cursor: pointer;
            font-size: 1rem;
            font-weight: 600;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        
        .control-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 25px rgba(99, 102, 241, 0.4);
        }}
        
        .control-btn:disabled {{
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }}
        
        .progress-bar {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 4px;
            background: rgba(99, 102, 241, 0.2);
            z-index: 101;
        }}
        
        .progress {{
            height: 100%;
            background: linear-gradient(90deg, #6366f1 0%, #8b5cf6 100%);
            transition: width 0.3s ease;
            width: 0%;
        }}
        
        .slide-number {{
            position: absolute;
            bottom: 20px;
            right: 30px;
            color: #64748b;
            font-size: 1rem;
        }}
        
        @keyframes slideIn {{
            from {{
                opacity: 0;
                transform: translateY(50px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        .slide-content li {{
            animation: fadeInUp 0.6s ease-out;
            animation-fill-mode: both;
        }}
        
        .slide-content li:nth-child(1) {{ animation-delay: 0.1s; }}
        .slide-content li:nth-child(2) {{ animation-delay: 0.2s; }}
        .slide-content li:nth-child(3) {{ animation-delay: 0.3s; }}
        .slide-content li:nth-child(4) {{ animation-delay: 0.4s; }}
        .slide-content li:nth-child(5) {{ animation-delay: 0.5s; }}
        
        @keyframes fadeInUp {{
            from {{
                opacity: 0;
                transform: translateY(20px);
            }}
            to {{
                opacity: 1;
                transform: translateY(0);
            }}
        }}
        
        @media (max-width: 768px) {{
            .slide {{
                padding: 30px;
                width: 95vw;
                height: 75vh;
            }}
            
            .slide-title {{
                font-size: 2rem;
            }}
            
            .slide-content {{
                font-size: 1.2rem;
            }}
            
            .controls {{
                flex-wrap: wrap;
                justify-content: center;
                bottom: 20px;
                padding: 12px 20px;
            }}
            
            .control-btn {{
                padding: 10px 16px;
                font-size: 0.9rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="progress-bar">
        <div class="progress" id="progress"></div>
    </div>
    
    <div class="presentation-container">
        <div class="header">
            <h1>🎯 {title}</h1>
            <div class="slide-counter" id="slideCounter">Слайд 1 из {len(slides)}</div>
        </div>
        
        <div class="slides-container" id="slidesContainer">
            {generate_slides_html(slides)}
        </div>
    </div>
    
    <div class="controls">
        <button class="control-btn" onclick="previousSlide()" id="prevBtn">
            ← Артқа
        </button>
        <button class="control-btn" onclick="toggleFullscreen()">
            📺 Толық экран
        </button>
        <button class="control-btn" onclick="nextSlide()" id="nextBtn">
            Алға →
        </button>
    </div>

    <script>
        let currentSlide = 0;
        const slides = document.querySelectorAll('.slide');
        const totalSlides = slides.length;
        const progress = document.getElementById('progress');
        const slideCounter = document.getElementById('slideCounter');
        const prevBtn = document.getElementById('prevBtn');
        const nextBtn = document.getElementById('nextBtn');
        
        function initPresentation() {{
            showSlide(currentSlide);
            updateControls();
            document.addEventListener('keydown', handleKeyPress);
        }}
        
        function showSlide(index) {{
            slides.forEach(slide => slide.classList.remove('active'));
            slides[index].classList.add('active');
            
            const progressPercent = ((index + 1) / totalSlides) * 100;
            progress.style.width = progressPercent + '%';
            
            slideCounter.textContent = `Слайд ${{index + 1}} из ${{totalSlides}}`;
            
            currentSlide = index;
            updateControls();
        }}
        
        function nextSlide() {{
            if (currentSlide < totalSlides - 1) {{
                showSlide(currentSlide + 1);
            }}
        }}
        
        function previousSlide() {{
            if (currentSlide > 0) {{
                showSlide(currentSlide - 1);
            }}
        }}
        
        function updateControls() {{
            prevBtn.disabled = currentSlide === 0;
            nextBtn.disabled = currentSlide === totalSlides - 1;
        }}
        
        function handleKeyPress(event) {{
            switch(event.key) {{
                case 'ArrowLeft':
                case 'PageUp':
                    previousSlide();
                    break;
                case 'ArrowRight':
                case 'PageDown':
                case ' ':
                    nextSlide();
                    break;
                case 'Home':
                    showSlide(0);
                    break;
                case 'End':
                    showSlide(totalSlides - 1);
                    break;
                case 'F11':
                    event.preventDefault();
                    toggleFullscreen();
                    break;
            }}
        }}
        
        function toggleFullscreen() {{
            if (!document.fullscreenElement) {{
                document.documentElement.requestFullscreen().catch(err => {{
                    console.log(`Толық экран режимін қосу кезіндегі қате: ${{err.message}}`);
                }});
            }} else {{
                if (document.exitFullscreen) {{
                    document.exitFullscreen();
                }}
            }}
        }}
        
        document.getElementById('slidesContainer').addEventListener('click', function(event) {{
            if (event.target.closest('.control-btn')) return;
            nextSlide();
        }});
        
        document.addEventListener('DOMContentLoaded', initPresentation);
        
        document.addEventListener('fullscreenchange', function() {{
            if (!document.fullscreenElement) {{
                console.log('Толық экран режимі өшірілді');
            }}
        }});
    </script>
</body>
</html>"""
    
    return html_content.encode('utf-8')

def parse_presentation_to_slides(text):
    slides = []
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    current_slide = None
    
    for line in lines:
        if (line.upper() == line and len(line) < 100) or any(keyword in line.lower() for keyword in ['слайд', 'slide', 'кіріспе', 'қорытынды']):
            if current_slide:
                slides.append(current_slide)
            current_slide = {'title': line, 'content': []}
        elif current_slide is not None:
            if line.startswith('•') or line.startswith('-') or (len(line) > 10 and not line.upper() == line):
                current_slide['content'].append(line)
    
    if current_slide:
        slides.append(current_slide)
    
    if not slides:
        slides = [{'title': 'Презентация', 'content': lines}]
    
    return slides

def generate_slides_html(slides):
    slides_html = ""
    for i, slide in enumerate(slides):
        slides_html += f"""
            <div class="slide" id="slide-{i}">
                <h1 class="slide-title">{slide['title']}</h1>
                <div class="slide-content">
                    {generate_slide_content(slide['content'])}
                </div>
                <div class="slide-number">{i + 1}</div>
            </div>
        """
    return slides_html

def generate_slide_content(content_lines):
    if not content_lines:
        return "<p>Ақпарат қосылады</p>"
    
    content_html = "<ul>"
    for line in content_lines[:10]:
        clean_line = line.lstrip('•- ').strip()
        if clean_line:
            content_html += f"<li>{clean_line}</li>"
    content_html += "</ul>"
    
    return content_html

PAGE_HTML = """<!DOCTYPE html>
<html lang="kk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Реферат және Презентация Генераторы</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
            color: #f8fafc;
            min-height: 100vh;
            line-height: 1.6;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        
        .header {
            text-align: center;
            margin-bottom: 40px;
            padding: 40px 0;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.1) 0%, rgba(16, 185, 129, 0.1) 100%);
            border-radius: 20px;
            border: 2px solid rgba(99, 102, 241, 0.2);
            position: relative;
            overflow: hidden;
        }
        
        .header::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: radial-gradient(circle at 30% 20%, rgba(99, 102, 241, 0.1) 0%, transparent 50%);
        }
        
        .header h1 {
            font-size: 3rem;
            font-weight: 700;
            background: linear-gradient(135deg, #6366f1 0%, #10b981 50%, #f59e0b 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 15px;
            position: relative;
        }
        
        .header p {
            font-size: 1.2rem;
            color: #94a3b8;
            font-weight: 500;
        }
        
        .card {
            background: linear-gradient(135deg, rgba(30, 41, 59, 0.95) 0%, rgba(15, 23, 42, 0.95) 100%);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 25px;
            border: 2px solid rgba(99, 102, 241, 0.2);
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(10px);
        }
        
        .card h2 {
            font-size: 1.8rem;
            font-weight: 600;
            margin-bottom: 20px;
            background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 20px;
        }
        
        @media (max-width: 768px) {
            .form-row {
                grid-template-columns: 1fr;
            }
        }
        
        label {
            display: block;
            font-weight: 500;
            margin-bottom: 8px;
            color: #cbd5e1;
            font-size: 0.95rem;
        }
        
        input, textarea, select {
            width: 100%;
            background: rgba(15, 23, 42, 0.8);
            border: 2px solid rgba(99, 102, 241, 0.3);
            border-radius: 12px;
            padding: 14px 16px;
            color: #f8fafc;
            font-size: 1rem;
            font-family: inherit;
            transition: all 0.3s ease;
        }
        
        input:focus, textarea:focus, select:focus {
            outline: none;
            border-color: #6366f1;
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
        }
        
        textarea {
            min-height: 150px;
            resize: vertical;
        }
        
        select {
            appearance: none;
            background-image: url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236366f1' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='m6 8 4 4 4-4'/%3e%3c/svg%3e");
            background-position: right 16px center;
            background-repeat: no-repeat;
            background-size: 16px;
            padding-right: 40px;
        }
        
        .btn {
            background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
            color: white;
            border: none;
            border-radius: 12px;
            padding: 14px 24px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            text-decoration: none;
        }
        
        .btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 25px rgba(99, 102, 241, 0.4);
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        
        .btn-secondary {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
        }
        
        .btn-accent {
            background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
        }
        
        .btn-danger {
            background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
        }
        
        .btn-ghost {
            background: transparent;
            border: 2px solid rgba(99, 102, 241, 0.3);
        }
        
        .btn-ghost:hover {
            background: rgba(99, 102, 241, 0.1);
        }
        
        .controls {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 20px;
        }
        
        .type-selector {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-bottom: 25px;
        }
        
        .type-btn {
            background: rgba(15, 23, 42, 0.8);
            border: 2px solid rgba(99, 102, 241, 0.3);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            cursor: pointer;
            transition: all 0.3s ease;
            font-weight: 600;
            font-size: 1.1rem;
        }
        
        .type-btn:hover {
            border-color: #6366f1;
            transform: translateY(-2px);
        }
        
        .type-btn.active {
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.2) 0%, rgba(79, 70, 229, 0.2) 100%);
            border-color: #6366f1;
        }
        
        .output {
            background: rgba(15, 23, 42, 0.8);
            border: 2px solid rgba(99, 102, 241, 0.3);
            border-radius: 12px;
            padding: 20px;
            min-height: 300px;
            white-space: pre-wrap;
            font-size: 1.1rem;
            line-height: 1.6;
            color: #e2e8f0;
            overflow-y: auto;
            max-height: 500px;
        }
        
        .status {
            padding: 12px 16px;
            border-radius: 8px;
            margin: 12px 0;
            font-weight: 500;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: #10b981;
        }
        
        .status.error {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #ef4444;
        }
        
        .user-info {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding: 20px;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.1) 0%, rgba(16, 185, 129, 0.1) 100%);
            border-radius: 12px;
            border: 2px solid rgba(99, 102, 241, 0.2);
        }
        
        .user-info h3 {
            font-size: 1.3rem;
            font-weight: 600;
        }
        
        .reports-list {
            display: grid;
            gap: 15px;
            margin-top: 20px;
        }
        
        .report-item {
            background: rgba(30, 41, 59, 0.8);
            border: 2px solid rgba(99, 102, 241, 0.2);
            border-radius: 12px;
            padding: 20px;
            transition: all 0.3s ease;
        }
        
        .report-item:hover {
            border-color: #6366f1;
            transform: translateY(-2px);
        }
        
        .type-badge {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
            margin-left: 10px;
            background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
            color: white;
        }
        
        .badge-presentation {
            background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%);
        }
        
        .auth-tabs {
            display: flex;
            margin-bottom: 25px;
            border-bottom: 2px solid rgba(99, 102, 241, 0.3);
        }
        
        .auth-tab {
            padding: 15px 30px;
            background: transparent;
            border: none;
            color: #64748b;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            border-bottom: 2px solid transparent;
        }
        
        .auth-tab.active {
            color: #6366f1;
            border-bottom-color: #6366f1;
        }
        
        .auth-form {
            display: none;
        }
        
        .auth-form.active {
            display: block;
        }
        
        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            border-top-color: #6366f1;
            animation: spin 1s ease-in-out infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .hidden {
            display: none !important;
        }
        
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 25px;
        }
        
        @media (max-width: 968px) {
            .grid {
                grid-template-columns: 1fr;
            }
        }
        
        footer {
            text-align: center;
            margin-top: 40px;
            padding: 20px;
            color: #64748b;
            font-size: 0.9rem;
        }
        
        @keyframes fadeIn {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        .card {
            animation: fadeIn 0.6s ease-out;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }
        
        .stat-card {
            background: rgba(30, 41, 59, 0.8);
            border: 2px solid rgba(99, 102, 241, 0.2);
            border-radius: 12px;
            padding: 25px;
            text-align: center;
            transition: all 0.3s ease;
        }
        
        .stat-card:hover {
            border-color: #6366f1;
            transform: translateY(-2px);
        }
        
        .stat-number {
            font-size: 2.5rem;
            font-weight: bold;
            margin-bottom: 8px;
        }
        
        .stat-label {
            color: #94a3b8;
            font-size: 0.9rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 AI Реферат және Презентация Генераторы</h1>
            <p>Жасанды интеллект көмегімен кәсіби жұмыстар жасаңыз</p>
        </div>

        <div id="authSection">
            <div class="card">
                <h2>🔐 Аутентификация</h2>
                
                <div class="auth-tabs">
                    <button class="auth-tab active" onclick="showAuthTab('login')">Жүйеге кіру</button>
                    <button class="auth-tab" onclick="showAuthTab('register')">Тіркелу</button>
                </div>

                <div id="loginForm" class="auth-form active">
                    <div class="form-group">
                        <label>👤 Пайдаланушы аты</label>
                        <input type="text" id="loginUsername" placeholder="Пайдаланушы атыңызды енгізіңіз">
                    </div>
                    <div class="form-group">
                        <label>🔒 Пароль</label>
                        <input type="password" id="loginPassword" placeholder="Пароліңізді енгізіңіз">
                    </div>
                    <button class="btn" onclick="login()" style="width: 100%">
                        Жүйеге кіру
                    </button>
                </div>

                <div id="registerForm" class="auth-form">
                    <div class="form-group">
                        <label>👤 Пайдаланушы аты</label>
                        <input type="text" id="regUsername" placeholder="Пайдаланушы атын ойлап табыңыз">
                    </div>
                    <div class="form-group">
                        <label>📧 Email (міндетті емес)</label>
                        <input type="email" id="regEmail" placeholder="Сіздің email адресіңіз">
                    </div>
                    <div class="form-group">
                        <label>🔒 Пароль</label>
                        <input type="password" id="regPassword" placeholder="Сенімді пароль ойлап табыңыз">
                    </div>
                    <div class="form-group">
                        <label>🔒 Парольді растау</label>
                        <input type="password" id="regPasswordConfirm" placeholder="Пароліңізді қайта енгізіңіз">
                    </div>
                    <button class="btn btn-secondary" onclick="register()" style="width: 100%">
                        Тіркелу
                    </button>
                </div>
            </div>
        </div>

        <div id="mainContent" class="hidden">
            <div class="card">
                <div class="user-info">
                    <div>
                        <h3>👋 Қош келдіңіз, <span id="currentUser">Пайдаланушы</span>!</h3>
                        <p style="color: #94a3b8; margin-top: 5px;">Керемет жұмыстар жасауға дайынсыз!</p>
                    </div>
                    <div style="display: flex; gap: 10px;">
                        <button class="btn btn-ghost" onclick="showAdminPanel()" id="adminBtn" style="display:none">
                            ⚙️ Админ-панель
                        </button>
                        <button class="btn btn-danger" onclick="logout()">
                            🚪 Шығу
                        </button>
                    </div>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h2>🎯 Контент жасау</h2>
                    
                    <div class="type-selector">
                        <div class="type-btn active" onclick="selectType('referat')">
                            📄 Реферат
                        </div>
                        <div class="type-btn" onclick="selectType('presentation')">
                            📊 Презентация
                        </div>
                    </div>
                    
                    <div class="form-row">
                        <div class="form-group">
                            <label>🌐 Контент тілі</label>
                            <select id="language">
                                <option value="kazakh">Қазақша</option>
                                <option value="russian">Русский</option>
                                <option value="english">English</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>📊 Сөздер саны</label>
                            <input type="number" id="wordCount" value="500" min="100" max="5000" step="50">
                            <small style="color: #94a3b8; font-size: 0.85rem;">Ұсынылады: 300-1000 сөз</small>
                        </div>
                    </div>
                    
                    <div class="form-group">
                        <label id="promptLabel">🎓 Реферат тақырыбы</label>
                        <textarea id="prompt" placeholder="Мысалы: Қазақстан тарихы, Жасанды интеллект, Ғарыштық технологиялар...">Қазақстан</textarea>
                    </div>
                    
                    <div class="controls">
                        <button class="btn" onclick="generate()" id="generateBtn">
                            <span class="loading" id="generateLoading" style="display:none"></span>
                            <span id="generateText">✨ Рефератты генерациялау</span>
                        </button>
                        <button class="btn btn-secondary" onclick="saveToProfile()">
                            📁 Профильде сақтау
                        </button>
                        <button class="btn btn-ghost" onclick="saveTxt()">
                            💾 TXT
                        </button>
                        <button class="btn btn-ghost" onclick="saveDocx()">
                            📄 DOCX
                        </button>
                        <button class="btn btn-accent" onclick="viewPresentation()" id="viewPresentationBtn" style="display:none">
                            👀 Презентацияны қарау
                        </button>
                    </div>
                </div>

                <div class="card">
                    <h2>📄 Нәтиже</h2>
                    <div id="status" class="status">Жұмысқа дайын. Тақырыпты енгізіп, "Генерациялау" түймесін басыңыз.</div>
                    <div id="output" class="output">
                        Мұнда генерацияланған мәтін пайда болады...
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>📂 Менің жұмыстарым</h2>
                <div id="reportsList" class="reports-list">
                    <div class="status">Сіздің жұмыстарыңыз жүктелуде...</div>
                </div>
            </div>
        </div>

        <div id="adminPanel" class="hidden">
            <div class="card">
                <h2>⚙️ Әкімшілік панелі</h2>
                <div class="controls">
                    <button class="btn" onclick="loadAdminStats()">📊 Жүйе статистикасы</button>
                    <button class="btn" onclick="loadAdminUsers()">👥 Пайдаланушылар</button>
                    <button class="btn" onclick="loadAdminReports()">📄 Барлық жұмыстар</button>
                    <button class="btn btn-ghost" onclick="hideAdminPanel()">← Генераторға оралу</button>
                </div>
                
                <div id="adminContent">
                    <div class="status">Әкімшілік ақпаратты көру үшін бөлімді таңдаңыз</div>
                </div>
            </div>
        </div>

        <footer>
            AI Generator ©️ 2025
        </footer>
    </div>

    <script>
        let currentUser = '';
        let currentUserId = null;
        let currentReport = '';
        let currentType = 'referat';
        let isAdminUser = false;

        function showAuthTab(tab) {
            document.querySelectorAll('.auth-tab').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.auth-form').forEach(form => form.classList.remove('active'));
            
            if (tab === 'login') {
                document.querySelector('.auth-tab:nth-child(1)').classList.add('active');
                document.getElementById('loginForm').classList.add('active');
            } else {
                document.querySelector('.auth-tab:nth-child(2)').classList.add('active');
                document.getElementById('registerForm').classList.add('active');
            }
        }

        async function register() {
            const username = document.getElementById('regUsername').value.trim();
            const email = document.getElementById('regEmail').value.trim();
            const password = document.getElementById('regPassword').value.trim();
            const passwordConfirm = document.getElementById('regPasswordConfirm').value.trim();
            
            if (!username || !password) {
                showNotification('Барлық міндетті өрістерді толтырыңыз', 'error');
                return;
            }
            
            if (password !== passwordConfirm) {
                showNotification('Парольдер сәйкес келмейді', 'error');
                return;
            }
            
            if (password.length < 4) {
                showNotification('Пароль кемінде 4 таңбадан тұруы керек', 'error');
                return;
            }
            
            try {
                const res = await fetch('/api/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({username, email, password})
                });
                
                const data = await res.json();
                if (data.success) {
                    showNotification('Тіркелу сәтті аяқталды! Енді жүйеге кіріңіз.', 'success');
                    showAuthTab('login');
                    document.getElementById('regUsername').value = '';
                    document.getElementById('regEmail').value = '';
                    document.getElementById('regPassword').value = '';
                    document.getElementById('regPasswordConfirm').value = '';
                } else {
                    showNotification(data.error || 'Тіркелу кезіндегі қате', 'error');
                }
            } catch (error) {
                showNotification('Желі қатесі', 'error');
            }
        }

        async function login() {
            const username = document.getElementById('loginUsername').value.trim();
            const password = document.getElementById('loginPassword').value.trim();
            
            if (!username || !password) {
                showNotification('Пайдаланушы аты мен парольді енгізіңіз', 'error');
                return;
            }
            
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({username, password})
                });
                
                const data = await res.json();
                if (data.success) {
                    currentUser = data.username;
                    currentUserId = data.user_id;
                    isAdminUser = (username === 'admin');
                    
                    document.getElementById('authSection').classList.add('hidden');
                    document.getElementById('mainContent').classList.remove('hidden');
                    document.getElementById('currentUser').textContent = currentUser;
                    
                    if (isAdminUser) {
                        document.getElementById('adminBtn').style.display = 'inline-block';
                    }
                    
                    showNotification('Сәтті кірдіңіз! Жүйеге қош келдіңіз.', 'success');
                    loadUserReports();
                    
                    document.getElementById('loginUsername').value = '';
                    document.getElementById('loginPassword').value = '';
                } else {
                    showNotification(data.error || 'Кіру қатесі', 'error');
                }
            } catch (error) {
                showNotification('Желі қатесі', 'error');
            }
        }

        function logout() {
            document.getElementById('authSection').classList.remove('hidden');
            document.getElementById('mainContent').classlassList.add('hidden');
            document.getElementById('adminPanel').classList.add('hidden');
            document.getElementById('adminBtn').style.display = 'none';
            currentUser = '';
            currentUserId = null;
            currentReport = '';
            isAdminUser = false;
            document.getElementById('output').textContent = 'Мұнда генерацияланған мәтін пайда болады...';
            document.getElementById('status').textContent = 'Жұмысқа дайын. Тақырыпты енгізіп, "Генерациялау" түймесін басыңыз.';
            showNotification('Сіз жүйеден шықтыңыз', 'success');
        }

        function selectType(type) {
            currentType = type;
            document.querySelectorAll('.type-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            if (type === 'referat') {
                document.getElementById('promptLabel').textContent = '🎓 Реферат тақырыбы';
                document.getElementById('generateText').textContent = '✨ Рефератты генерациялау';
                document.getElementById('viewPresentationBtn').style.display = 'none';
            } else {
                document.getElementById('promptLabel').textContent = '📊 Презентация тақырыбы';
                document.getElementById('generateText').textContent = '✨ Презентацияны генерациялау';
                document.getElementById('viewPresentationBtn').style.display = 'inline-block';
            }
        }

        function showAdminPanel() {
            if (!isAdminUser) {
                showNotification('Доступ запрещен. Только для администраторов.', 'error');
                return;
            }
            
            document.getElementById('mainContent').classList.add('hidden');
            document.getElementById('adminPanel').classList.remove('hidden');
        }

        function hideAdminPanel() {
            document.getElementById('adminPanel').classList.add('hidden');
            document.getElementById('mainContent').classList.remove('hidden');
        }

        async function generate() {
            const prompt = document.getElementById('prompt').value.trim();
            const language = document.getElementById('language').value;
            const wordCount = parseInt(document.getElementById('wordCount').value) || 500;
            
            if (!prompt) {
                showNotification('Генерация үшін тақырыпты енгізіңіз', 'error');
                return;
            }
            
            if (wordCount < 100 || wordCount > 5000) {
                showNotification('Сөздер саны 100-ден 5000-ға дейін болуы керек', 'error');
                return;
            }
            
            const generateBtn = document.getElementById('generateBtn');
            const loadingElem = document.getElementById('generateLoading');
            const generateText = document.getElementById('generateText');
            
            generateBtn.disabled = true;
            loadingElem.style.display = 'inline-block';
            generateText.textContent = 'Генерация...';
            
            document.getElementById('status').textContent = '🔄 AI-ге сұрау... Күте тұрыңыз.';
            
            try {
                const res = await fetch('/api/generate', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        prompt, 
                        type: currentType,
                        language: language,
                        word_count: wordCount
                    })
                });
                
                const data = await res.json();
                currentReport = data.text || 'Генерация қатесі.';
                
                const actualWordCount = currentReport.split(/\s+/).length;
                document.getElementById('output').textContent = currentReport;
                document.getElementById('status').textContent = `✅ Сәтті генерацияланды! ${actualWordCount} сөз (мақсат: ${wordCount})`;
                showNotification(`${currentType === 'referat' ? 'Реферат' : 'Презентация'} сәтті генерацияланды!`, 'success');
                
            } catch (error) {
                document.getElementById('status').textContent = '❌ Генерация кезіндегі қате: ' + error;
                showNotification('Контентті генерациялау кезіндегі қате', 'error');
            } finally {
                generateBtn.disabled = false;
                loadingElem.style.display = 'none';
                generateText.textContent = currentType === 'referat' ? '✨ Рефератты генерациялау' : '✨ Презентацияны генерациялау';
            }
        }

        async function saveTxt() {
            if (!currentReport) {
                showNotification('Алдымен контентті генерациялаңыз', 'error');
                return;
            }
            
            try {
                const title = document.getElementById('prompt').value.trim() || 'document';
                const filename = currentType === 'referat' ? `реферат_${title}.txt` : `презентация_${title}.txt`;
                
                const blob = new Blob([currentReport], { type: 'text/plain; charset=utf-8' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                a.click();
                URL.revokeObjectURL(url);
                
                showNotification('TXT файлы сәтті жүктеп алынды!', 'success');
            } catch (error) {
                showNotification('Файлды жүктеп алу кезіндегі қате', 'error');
            }
        }

        async function saveDocx() {
            if (!currentReport) {
                showNotification('Алдымен контентті генерациялаңыз', 'error');
                return;
            }
            
            try {
                const title = document.getElementById('prompt').value.trim() || 'Құжат';
                const res = await fetch('/api/save_docx', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({text: currentReport, type: currentType, title: title})
                });
                
                if (res.ok) {
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = currentType === 'referat' ? 'реферат.docx' : 'презентация.docx';
                    a.click();
                    URL.revokeObjectURL(url);
                    showNotification('DOCX файлы сәтті жүктеп алынды!', 'success');
                } else {
                    showNotification('DOCX файлын жасау кезіндегі қате', 'error');
                }
            } catch (error) {
                showNotification('DOCX жүктеп алу кезіндегі қате', 'error');
            }
        }

        async function saveToProfile() {
            if (!currentReport) {
                showNotification('Алдымен контентті генерациялаңыз', 'error');
                return;
            }
            
            try {
                const title = document.getElementById('prompt').value.trim() || 'Атауы жоқ';
                const language = document.getElementById('language').value;
                const wordCount = currentReport.split(/\s+/).length;
                
                const res = await fetch('/api/save_report', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        title, 
                        content: currentReport, 
                        type: currentType,
                        language: language,
                        word_count: wordCount
                    })
                });
                
                const data = await res.json();
                if (data.success) {
                    showNotification('Жұмыс сіздің профиліңізде сәтті сақталды!', 'success');
                    loadUserReports();
                } else {
                    showNotification(data.error || 'Сақтау қатесі', 'error');
                }
            } catch (error) {
                showNotification('Желі қатесі', 'error');
            }
        }

        async function loadUserReports() {
            try {
                const res = await fetch('/api/get_reports');
                const data = await res.json();
                
                const reportsList = document.getElementById('reportsList');
                if (data.reports && data.reports.length > 0) {
                    reportsList.innerHTML = data.reports.map(report => `
                        <div class="report-item">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                                <div style="font-weight: 600; font-size: 1.1rem;">${report.title}
                                    <span class="type-badge ${report.type === 'presentation' ? 'badge-presentation' : ''}">
                                        ${report.type === 'presentation' ? '📊' : '📄'}
                                    </span>
                                </div>
                                <div style="color: #94a3b8; font-size: 0.9rem;">${new Date(report.created_at).toLocaleString('kk-KZ')}</div>
                            </div>
                            <div style="color: #94a3b8; font-size: 0.9rem; margin-bottom: 10px;">
                                🌐 ${report.language || 'kazakh'} | 📊 ${report.word_count || 'көрсетілмеген'} сөз
                            </div>
                            <div style="margin-bottom: 15px; color: #cbd5e1;">${report.content.substring(0, 150)}...</div>
                            <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                                <button class="btn btn-ghost" onclick="downloadReport(${report.id}, 'txt')" style="padding: 8px 16px; font-size: 0.9rem;">📥 TXT</button>
                                <button class="btn btn-ghost" onclick="downloadReport(${report.id}, 'docx')" style="padding: 8px 16px; font-size: 0.9rem;">📥 DOCX</button>
                                ${report.type === 'presentation' ? `<button class="btn btn-accent" onclick="viewSavedPresentation(${report.id})" style="padding: 8px 16px; font-size: 0.9rem;">👀 Қарау</button>` : ''}
                            </div>
                        </div>
                    `).join('');
                } else {
                    reportsList.innerHTML = '<div class="status">📝 Сізде әлі сақталған жұмыстар жоқ. Бірінші жұмысыңызды генерациялап, сақтаңыз!</div>';
                }
            } catch (error) {
                showNotification('Жұмыстар тізімін жүктеу кезіндегі қате', 'error');
            }
        }

        async function downloadReport(reportId, format) {
            try {
                const res = await fetch(`/api/download_report/${reportId}/${format}`);
                if (res.ok) {
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    
                    const reportType = await getReportType(reportId);
                    const prefix = reportType === 'presentation' ? 'презентация' : 'реферат';
                    const extension = format === 'docx' ? 'docx' : 'txt';
                    
                    a.download = `${prefix}_${reportId}.${extension}`;
                    a.click();
                    URL.revokeObjectURL(url);
                    showNotification('Файл сәтті жүктеп алынды!', 'success');
                } else {
                    showNotification('Файлды жүктеп алу кезіндегі қате', 'error');
                }
            } catch (error) {
                showNotification('Жүктеп алу кезіндегі қате', 'error');
            }
        }

        async function getReportType(reportId) {
            try {
                const res = await fetch('/api/get_reports');
                const data = await res.json();
                const report = data.reports.find(r => r.id === reportId);
                return report ? report.type : 'referat';
            } catch (error) {
                return 'referat';
            }
        }

        async function viewSavedPresentation(reportId) {
            try {
                const res = await fetch('/api/get_reports');
                const data = await res.json();
                const report = data.reports.find(r => r.id === reportId);
                
                if (report && report.type === 'presentation') {
                    const form = document.createElement('form');
                    form.method = 'POST';
                    form.action = '/api/view_presentation';
                    form.target = '_blank';
                    
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = 'content';
                    input.value = report.content;
                    
                    const titleInput = document.createElement('input');
                    titleInput.type = 'hidden';
                    titleInput.name = 'title';
                    titleInput.value = report.title;
                    
                    form.appendChild(input);
                    form.appendChild(titleInput);
                    document.body.appendChild(form);
                    form.submit();
                    document.body.removeChild(form);
                }
            } catch (error) {
                showNotification('Презентацияны ашу кезіндегі қате', 'error');
            }
        }

        function viewPresentation() {
            if (!currentReport) {
                showNotification('Алдымен презентацияны генерациялаңыз', 'error');
                return;
            }
            
            const title = document.getElementById('prompt').value.trim() || 'Презентация';
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = '/api/view_presentation';
            form.target = '_blank';
            
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'content';
            input.value = currentReport;
            
            const titleInput = document.createElement('input');
            titleInput.type = 'hidden';
            titleInput.name = 'title';
            titleInput.value = title;
            
            form.appendChild(input);
            form.appendChild(titleInput);
            document.body.appendChild(form);
            form.submit();
            document.body.removeChild(form);
        }

        async function loadAdminStats() {
            if (!isAdminUser) return;
            
            try {
                const res = await fetch('/api/admin/stats');
                const data = await res.json();
                
                document.getElementById('adminContent').innerHTML = `
                    <div class="stats-grid">
                        <div class="stat-card">
                            <div class="stat-number" style="color: #6366f1;">${data.user_count}</div>
                            <div class="stat-label">👥 Пайдаланушылар</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-number" style="color: #10b981;">${data.report_count}</div>
                            <div class="stat-label">📄 Рефераттар</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-number" style="color: #f59e0b;">${data.presentation_count}</div>
                            <div class="stat-label">📊 Презентациялар</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-number" style="color: #8b5cf6;">${data.today_reports}</div>
                            <div class="stat-label">📅 Бүгін</div>
                        </div>
                    </div>
                    <div class="status" style="margin-top: 20px;">
                        Соңғы тіркелген пайдаланушы: <strong>${data.last_user}</strong>
                    </div>
                `;
            } catch (error) {
                showNotification('Статистиканы жүктеу кезіндегі қате', 'error');
            }
        }

        async function loadAdminUsers() {
            if (!isAdminUser) return;
            
            try {
                const res = await fetch('/api/admin/users');
                const data = await res.json();
                
                document.getElementById('adminContent').innerHTML = `
                    <div style="margin-top: 20px;">
                        <h3 style="margin-bottom: 15px; color: #e2e8f0;">Пайдаланушылар тізімі</h3>
                        ${data.users.map(user => `
                            <div class="report-item" style="margin-bottom: 10px;">
                                <div><strong>${user.username}</strong> (${user.email || 'email жоқ'})</div>
                                <div style="color: #94a3b8; font-size: 0.9rem;">Тіркелген: ${new Date(user.created_at).toLocaleString('kk-KZ')}</div>
                            </div>
                        `).join('')}
                    </div>
                `;
            } catch (error) {
                showNotification('Пайдаланушыларды жүктеу кезіндегі қате', 'error');
            }
        }

        async function loadAdminReports() {
            if (!isAdminUser) return;
            
            try {
                const res = await fetch('/api/admin/reports');
                const data = await res.json();
                
                if (data.reports && data.reports.length > 0) {
                    document.getElementById('adminContent').innerHTML = `
                        <div style="margin-top: 20px;">
                            <h3 style="margin-bottom: 15px; color: #e2e8f0;">Жүйедегі барлық жұмыстар</h3>
                            ${data.reports.map(report => `
                                <div class="report-item" style="margin-bottom: 10px;">
                                    <div><strong>${report.title}</strong> 
                                        <span class="type-badge ${report.type === 'presentation' ? 'badge-presentation' : ''}">
                                            ${report.type === 'presentation' ? '📊 Презентация' : '📄 Реферат'}
                                        </span>
                                    </div>
                                    <div>Автор: <strong>${report.username}</strong></div>
                                    <div style="color: #94a3b8; font-size: 0.9rem;">Тілі: ${report.language || 'kazakh'} | Сөздер: ${report.word_count || 'көрсетілмеген'}</div>
                                    <div style="color: #94a3b8; font-size: 0.9rem;">Жасалған: ${new Date(report.created_at).toLocaleString('kk-KZ')}</div>
                                </div>
                            `).join('')}
                        </div>
                    `;
                } else {
                    document.getElementById('adminContent').innerHTML = '<div class="status">📝 Жүйеде әлі жұмыстар жоқ.</div>';
                }
            } catch (error) {
                showNotification('Жұмыстарды жүктеу кезіндегі қате', 'error');
            }
        }

        function showNotification(message, type) {
            const notification = document.createElement('div');
            notification.className = `status ${type === 'error' ? 'error' : ''}`;
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                z-index: 1000;
                min-width: 300px;
                animation: fadeIn 0.3s ease-out;
            `;
            notification.textContent = message;
            
            document.body.appendChild(notification);
            
            setTimeout(() => {
                if (document.body.contains(notification)) {
                    notification.style.animation = 'fadeOut 0.3s ease-out';
                    setTimeout(() => {
                        if (document.body.contains(notification)) {
                            document.body.removeChild(notification);
                        }
                    }, 300);
                }
            }, 3000);
        }

        const style = document.createElement('style');
        style.textContent = `
            @keyframes fadeOut {
                from { opacity: 1; transform: translateY(0); }
                to { opacity: 0; transform: translateY(-20px); }
            }
        `;
        document.head.appendChild(style);
    </script>
</body>
</html>
"""

def respond_json(start_response, obj, status="200 OK", headers=None):
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    hdrs = [("Content-Type", "application/json; charset=utf-8")]
    if headers:
        hdrs.extend(headers)
    start_response(status, hdrs)
    return [payload]

def respond_text(start_response, text, content_type="text/html; charset=utf-8"):
    data = text.encode("utf-8")
    start_response("200 OK", [("Content-Type", content_type)])
    return [data]

def parse_body(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError:
        length = 0
    body = environ["wsgi.input"].read(length) if length > 0 else b""
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {}

def get_session_user(environ):
    cookies = environ.get('HTTP_COOKIE', '')
    if 'user_id=' in cookies:
        for cookie in cookies.split(';'):
            if 'user_id=' in cookie.strip():
                user_id = cookie.split('=')[1].strip()
                conn = sqlite3.connect('reports.db', check_same_thread=False)
                cursor = conn.cursor()
                cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
                user = cursor.fetchone()
                conn.close()
                if user:
                    return user[0]
    return None

def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")
    
    print(f"Request: {method} {path}")

    if path == "/" and method == "GET":
        return respond_text(start_response, PAGE_HTML)

    elif path == "/api/register" and method == "POST":
        data = parse_body(environ)
        username = data.get("username", "")
        email = data.get("email", "")
        password = data.get("password", "")
        
        if not username or not password:
            return respond_json(start_response, {"success": False, "error": "Барлық міндетті өрістерді толтырыңыз"})
        
        result = register_user(username, password, email)
        return respond_json(start_response, result)

    elif path == "/api/login" and method == "POST":
        data = parse_body(environ)
        username = data.get("username", "")
        password = data.get("password", "")
        
        if not username or not password:
            return respond_json(start_response, {"success": False, "error": "Барлық өрістерді толтырыңыз"})
        
        result = login_user(username, password)
        if result["success"]:
            headers = [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Set-Cookie", f"user_id={result['user_id']}; Path=/")
            ]
            payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
            start_response("200 OK", headers)
            return [payload]
        else:
            return respond_json(start_response, result)

    elif path == "/api/generate" and method == "POST":
        data = parse_body(environ)
        prompt = data.get("prompt", "")
        content_type = data.get("type", "referat")
        language = data.get("language", "kazakh")
        word_count = data.get("word_count", 500)
        
        text = call_openrouter(AI_API_KEY, prompt, content_type, language, word_count)
        return respond_json(start_response, {"text": text})

    elif path == "/api/save_txt" and method == "POST":
        data = parse_body(environ)
        text = data.get("text", "")
        file_data = make_formatted_txt(text)
        start_response("200 OK", [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Disposition", "attachment; filename=content.txt")
        ])
        return [file_data]

    elif path == "/api/save_docx" and method == "POST":
        data = parse_body(environ)
        text = data.get("text", "")
        content_type = data.get("type", "referat")
        title = data.get("title", "Реферат")
        
        if content_type == "presentation":
            file_data = make_presentation_docx(text, title)
        else:
            file_data = make_docx(text, title)
            
        start_response("200 OK", [
            ("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("Content-Disposition", f"attachment; filename={'реферат' if content_type == 'referat' else 'презентация'}.docx")
        ])
        return [file_data]

    elif path == "/api/save_presentation_docx" and method == "POST":
        try:
            if environ.get('CONTENT_TYPE', '').startswith('application/x-www-form-urlencoded'):
                length = int(environ.get('CONTENT_LENGTH', 0))
                body = environ['wsgi.input'].read(length).decode('utf-8')
                data = parse_qs(body)
                content = data.get('content', [''])[0]
                title = data.get('title', ['Презентация'])[0]
            else:
                data = parse_body(environ)
                content = data.get('content', '')
                title = data.get('title', 'Презентация')
                
            file_data = make_presentation_docx(content, title)
            start_response("200 OK", [
                ("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                ("Content-Disposition", f"attachment; filename=презентация_{title.replace(' ', '_')}.docx")
            ])
            return [file_data]
        except Exception as e:
            return respond_json(start_response, {"error": str(e)})

    elif path == "/api/save_presentation_pdf" and method == "POST":
        try:
            if environ.get('CONTENT_TYPE', '').startswith('application/x-www-form-urlencoded'):
                length = int(environ.get('CONTENT_LENGTH', 0))
                body = environ['wsgi.input'].read(length).decode('utf-8')
                data = parse_qs(body)
                content = data.get('content', [''])[0]
                title = data.get('title', ['Презентация'])[0]
            else:
                data = parse_body(environ)
                content = data.get('content', '')
                title = data.get('title', 'Презентация')
                
            file_data = make_presentation_pdf(content, title)
            start_response("200 OK", [
                ("Content-Type", "application/pdf"),
                ("Content-Disposition", f"attachment; filename=презентация_{title.replace(' ', '_')}.pdf")
            ])
            return [file_data]
        except Exception as e:
            return respond_json(start_response, {"error": str(e)})

    elif path == "/api/view_presentation" and method == "POST":
        try:
            if environ.get('CONTENT_TYPE', '').startswith('application/x-www-form-urlencoded'):
                length = int(environ.get('CONTENT_LENGTH', 0))
                body = environ['wsgi.input'].read(length).decode('utf-8')
                data = parse_qs(body)
                content = data.get('content', [''])[0]
                title = data.get('title', ['Презентация'])[0]
            else:
                data = parse_body(environ)
                content = data.get('content', '')
                title = data.get('title', 'Презентация')
                
            html_content = make_presentation_html(content, title)
            return respond_text(start_response, html_content.decode('utf-8'))
        except Exception as e:
            return respond_json(start_response, {"error": str(e)})

    elif path == "/api/save_report" and method == "POST":
        user = get_session_user(environ)
        if not user:
            return respond_json(start_response, {"success": False, "error": "Аутентификация қажет"})
        
        data = parse_body(environ)
        title = data.get("title", "")
        content = data.get("content", "")
        content_type = data.get("type", "referat")
        language = data.get("language", "kazakh")
        word_count = data.get("word_count", 0)
        
        conn = sqlite3.connect('reports.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (user,))
        user_data = cursor.fetchone()
        
        if user_data:
            report_id = save_report_to_db(user_data[0], title, content, content_type, language, word_count)
            return respond_json(start_response, {"success": True, "report_id": report_id})
        else:
            return respond_json(start_response, {"success": False, "error": "Пайдаланушы табылмады"})

    elif path == "/api/get_reports" and method == "GET":
        user = get_session_user(environ)
        if not user:
            return respond_json(start_response, {"reports": []})
        
        conn = sqlite3.connect('reports.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (user,))
        user_data = cursor.fetchone()
        
        if user_data:
            reports = get_user_reports(user_data[0])
            reports_data = [{
                "id": r[0],
                "title": r[1],
                "content": r[2],
                "type": r[3],
                "language": r[4],
                "word_count": r[5],
                "created_at": r[6]
            } for r in reports]
            return respond_json(start_response, {"reports": reports_data})
        else:
            return respond_json(start_response, {"reports": []})

    elif path.startswith("/api/download_report/") and method == "GET":
        user = get_session_user(environ)
        if not user:
            return respond_json(start_response, {"error": "Аутентификация қажет"}, status="401 Unauthorized")
        
        parts = path.split("/")
        if len(parts) >= 5:
            report_id = parts[3]
            file_format = parts[4]
            
            conn = sqlite3.connect('reports.db', check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.content, r.title, r.type 
                FROM reports r 
                JOIN users u ON r.user_id = u.id 
                WHERE r.id = ? AND u.username = ?
            """, (report_id, user))
            report = cursor.fetchone()
            conn.close()
            
            if report:
                content, title, report_type = report
                if file_format == "txt":
                    file_data = make_formatted_txt(content)
                    start_response("200 OK", [
                        ("Content-Type", "text/plain; charset=utf-8"),
                        ("Content-Disposition", f"attachment; filename={'реферат' if report_type == 'referat' else 'презентация'}_{report_id}.txt")
                    ])
                    return [file_data]
                elif file_format == "docx":
                    if report_type == "presentation":
                        file_data = make_presentation_docx(content, title)
                    else:
                        file_data = make_docx(content, title)
                    start_response("200 OK", [
                        ("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                        ("Content-Disposition", f"attachment; filename={'реферат' if report_type == 'referat' else 'презентация'}_{report_id}.docx")
                    ])
                    return [file_data]
        
        return respond_json(start_response, {"error": "Файл табылмады"}, status="404 Not Found")

    elif path == "/api/admin/stats" and method == "GET":
        user = get_session_user(environ)
        if not user or not is_admin(user):
            return respond_json(start_response, {"error": "Доступ запрещен"}, status="403 Forbidden")
        
        stats = get_db_stats()
        return respond_json(start_response, stats)

    elif path == "/api/admin/users" and method == "GET":
        user = get_session_user(environ)
        if not user or not is_admin(user):
            return respond_json(start_response, {"error": "Доступ запрещен"}, status="403 Forbidden")
        
        users = get_all_users()
        users_data = [{
            "id": u[0],
            "username": u[1],
            "email": u[2],
            "created_at": u[3]
        } for u in users]
        return respond_json(start_response, {"users": users_data})

    elif path == "/api/admin/reports" and method == "GET":
        user = get_session_user(environ)
        if not user or not is_admin(user):
            return respond_json(start_response, {"error": "Доступ запрещен"}, status="403 Forbidden")
        
        reports = get_all_reports()
        reports_data = [{
            "id": r[0],
            "username": r[1],
            "title": r[2],
            "content": r[3],
            "type": r[4],
            "language": r[5],
            "word_count": r[6],
            "created_at": r[7]
        } for r in reports]
        return respond_json(start_response, {"reports": reports_data})

    else:
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"404 Not Found"]

if __name__ == "__main__":
    init_db()
    port = 8000
    print(f"🚀 Сервер {port} портында іске қосылуда...")
    print("🌐 Браузерде http://localhost:8000 ашыңыз")
    
    try:
        with make_server("", port, app) as httpd:
            print(f"✅ Сервер сәтті іске қосылды!")
            print("🎨 Түзетілген дизайн:")
            print("   • Енгізу өрістерінің бірыңғай стилі")
            print("   • 'Профильде сақтау' түймесі жұмыс істейді")
            print("   • 'Барлық жұмыстар' админ-панелі жұмыс істейді")
            print("👥 Пайдаланушыларды тіркеу және авторизация")
            print("🔑 Админ: admin / admin123")
            print("🤖 AI интеграциясы: OpenRouter API")
            print("🌐 Тілдерді қолдау: қазақ, орыс, ағылшын")
            print("📊 Көлемді бақылау: 100-5000 сөз")
            print("📄 Рефераттар мен презентациялар")
            print("💾 Сақтау: TXT, DOCX, профильде")
            httpd.serve_forever()
    except Exception as e:
        print(f"❌ Серверді іске қосу кезіндегі қате: {e}")