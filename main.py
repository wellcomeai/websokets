// ================ Утилиты и конфигурация ================
const SERVER_URL = window.location.origin;
const WS_PROXY_URL = `${SERVER_URL.replace('http', 'ws')}/ws_proxy`;
let ws = null;
let sessionInfo = null;
let audioContext = null;
let micStream = null;
let scriptProcessor = null;
let micEnabled = false;
let currentResponseText = "";
let reconnectAttempts = 0;
let maxReconnectAttempts = 3;
let isListening = false;

// Проверяем и создаем аудио-элемент
function ensureAudioElement() {
  let audioEl = document.getElementById('ttsAudio');
  if (!audioEl) {
    log("⚠️ Аудио-элемент не найден, создаем новый");
    audioEl = document.createElement('audio');
    audioEl.id = 'ttsAudio';
    audioEl.style.display = 'none';
    document.body.appendChild(audioEl);
  }
  return audioEl;
}

const log = (msg) => {
  console.log(msg);
  const box = document.getElementById('log');
  box.textContent += `\n${msg}`;
  box.scrollTop = box.scrollHeight;
};

const showError = (message) => {
  const errorBox = document.getElementById('errorBox');
  errorBox.textContent = message;
  errorBox.style.display = 'block';
  setTimeout(() => {
    errorBox.style.display = 'none';
  }, 5000);
  console.error("Error:", message);
};

const updateStatus = (message) => {
  const statusElement = document.getElementById('status');
  statusElement.textContent = message;
  
  // Добавляем анимацию
  statusElement.classList.remove('status-animation');
  void statusElement.offsetWidth; // Перезапуск анимации
  statusElement.classList.add('status-animation');
};

// ================ API взаимодействие ================
async function createSession() {
  try {
    updateStatus("Создание сессии...");
    const voiceValue = document.getElementById('voiceSelect').value;
    
    const response = await fetch(`${SERVER_URL}/create_session`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        voice: voiceValue,
        // Остальные параметры используются по умолчанию
      })
    });
    
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Ошибка создания сессии: ${errorText}`);
    }
    
    sessionInfo = await response.json();
    log(`✅ Сессия создана: ${sessionInfo.sessionId}`);
    log(`🔊 Голос: ${sessionInfo.voice}`);
    
    return sessionInfo;
  } catch (error) {
    log(`❌ Ошибка: ${error.message}`);
    showError(`Не удалось создать сессию: ${error.message}`);
    throw error;
  }
}

// ================ WebSocket соединение (через прокси) ================
function connectToProxy(sessionData) {
  try {
    updateStatus("Подключение к серверу...");
    
    // Используем прокси-подключение через наш сервер
    const wsUrl = `${WS_PROXY_URL}/${encodeURIComponent(sessionData.clientSecret)}`;
    log(`🔌 Подключение к WebSocket прокси: ${wsUrl}`);
    
    const socket = new WebSocket(wsUrl);
    
    socket.onopen = async () => {
      log("🔌 WebSocket подключен");
      updateStatus("Соединение установлено");
      reconnectAttempts = 0; // Сбрасываем счётчик переподключений
      
      // Запускаем микрофон
      await startMicrophone();
    };
    
    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log(`📦 Получено событие:`, data);
        
        switch (data.type) {
          case "session.created":
            log("📝 Сессия инициализирована");
            break;
            
          case "session.updated":
            log("📝 Сессия обновлена");
            break;
            
          case "error":
            log(`❌ Ошибка: ${data.error?.message || JSON.stringify(data.error)}`);
            showError(data.error?.message || "Произошла ошибка");
            break;
            
          case "input_audio_buffer.speech_started":
            log("🗣️ Обнаружена речь");
            setMicrophoneListening(true);
            break;
            
          case "input_audio_buffer.speech_stopped":
            log("🎤 Конец речи");
            setMicrophoneListening(false);
            break;
            
          case "conversation.item.input_audio_transcription.completed":
            log(`📝 Транскрипция: ${data.transcript}`);
            updateStatus(`Вы: ${data.transcript}`);
            currentResponseText = "";
            break;
            
          case "response.created":
            log("🤖 Начало ответа");
            break;
            
          case "response.text.delta":
            currentResponseText += data.delta;
            if (data.delta.trim() !== "") {
              log(`📤 ${data.delta}`);
            }
            updateStatus(`Jarvis: ${currentResponseText}`);
            break;
            
          case "response.done":
            log("✅ Ответ завершен");
            // Синтезируем полный текст ответа через TTS API
            if (currentResponseText.length > 0) {
              playTextAsTTS(currentResponseText);
            }
            break;
            
          default:
            // Игнорируем другие типы событий
            break;
        }
      } catch (e) {
        log(`❌ Ошибка обработки сообщения: ${e.message}`);
        console.error("Ошибка обработки сообщения WebSocket:", e);
      }
    };
    
    socket.onerror = (error) => {
      log(`❌ WebSocket ошибка`);
      console.error("WebSocket error:", error);
      showError("Ошибка WebSocket соединения");
    };
    
    socket.onclose = (event) => {
      log(`🔌 WebSocket закрыт, код: ${event.code}, причина: ${event.reason || 'нет данных'}`);
      updateStatus("Соединение закрыто");
      stopMicrophone();
      
      // Пытаемся переподключиться, если соединение было закрыто неожиданно
      if (event.code !== 1000 && event.code !== 1001 && reconnectAttempts < maxReconnectAttempts) {
        reconnectAttempts++;
        const timeout = reconnectAttempts * 2000;
        log(`🔄 Попытка переподключения ${reconnectAttempts}/${maxReconnectAttempts} через ${timeout/1000} сек.`);
        setTimeout(() => {
          ws = connectToProxy(sessionInfo);
        }, timeout);
      }
    };
    
    return socket;
  } catch (error) {
    log(`❌ Ошибка подключения: ${error.message}`);
    showError(`Ошибка подключения: ${error.message}`);
    throw error;
  }
}

// ================ Управление микрофоном ================
async function startMicrophone() {
  try {
    if (micEnabled) return;
    
    log("🎤 Запуск микрофона...");
    
    // Проверяем поддержку браузером
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("Ваш браузер не поддерживает доступ к микрофону");
    }
    
    // Инициализация AudioContext и получение потока микрофона
    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    
    log("🎤 Запрос доступа к микрофону...");
    updateStatus("Запрос доступа к микрофону...");
    
    const stream = await navigator.mediaDevices.getUserMedia({ 
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      } 
    });
    
    log("✅ Доступ к микрофону получен");
    micStream = stream;
    
    // Создаем источник из потока микрофона
    const micSource = audioContext.createMediaStreamSource(stream);
    
    // Создаем процессор для обработки аудио
    scriptProcessor = audioContext.createScriptProcessor(4096, 1, 1);
    
    // Переменные для подавления шума
    let silenceTimer = null;
    let isSpeaking = false;
    let audioCounter = 0;
    let noSoundCounter = 0;
    let lastAudioSent = Date.now();
    
    scriptProcessor.onaudioprocess = (audioProcessingEvent) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          const inputBuffer = audioProcessingEvent.inputBuffer;
          const inputData = inputBuffer.getChannelData(0);
          
          // Проверяем уровень звука
          let soundLevel = 0;
          for (let i = 0; i < inputData.length; i++) {
            soundLevel += Math.abs(inputData[i]);
          }
          soundLevel = soundLevel / inputData.length;
          
          // Используем порог для определения речи
          const threshold = 0.01;
          const currentTime = Date.now();
          
          if (soundLevel > threshold) {
            noSoundCounter = 0;
            audioCounter++;
            
            // Отправляем аудио только если оно содержит достаточно звука
            if (audioCounter > 3 && (!isSpeaking || audioCounter < 10 || (currentTime - lastAudioSent > 500))) {
              lastAudioSent = currentTime;
              
              // Преобразуем float32 в int16
              const pcmBuffer = new Int16Array(inputData.length);
              for (let i = 0; i < inputData.length; i++) {
                // Конвертация из float [-1.0,1.0] в int16 [-32768,32767]
                pcmBuffer[i] = Math.max(-32768, Math.min(32767, Math.floor(inputData[i] * 32768)));
              }
              
              // Преобразуем в base64 и отправляем
              const binary = new Uint8Array(pcmBuffer.buffer);
              const base64 = btoa(String.fromCharCode(...new Uint8Array(binary.buffer)));
              
              // Отправляем аудиоданные только если уровень звука достаточный
              ws.send(JSON.stringify({
                type: "input_audio_buffer.append",
                audio: base64
              }));
              
              if (!isSpeaking) {
                isSpeaking = true;
                setMicrophoneListening(true);
                log("🎙️ Обнаружен звук, аудио отправляется...");
                
                // Очищаем предыдущий таймер тишины, если он есть
                if (silenceTimer) {
                  clearTimeout(silenceTimer);
                  silenceTimer = null;
                }
              }
              
              // Визуальная индикация уровня звука
              const micPulse = document.getElementById('micPulse');
              const intensity = Math.min(50, soundLevel * 1000);
              micPulse.style.boxShadow = `0 0 ${25 + intensity}px #00FF7F`;
            }
          } else {
            // Увеличиваем счетчик тишины
            noSoundCounter++;
            
            // Если тишина достаточно долгая, останавливаем запись
            if (isSpeaking && noSoundCounter > 30) { // ~30 фреймов = ~600мс тишины
              if (!silenceTimer) {
                silenceTimer = setTimeout(() => {
                  isSpeaking = false;
                  audioCounter = 0;
                  setMicrophoneListening(false);
                  log("🎙️ Тишина, запись приостановлена");
                  silenceTimer = null;
                  document.getElementById('micPulse').style.boxShadow = "0 0 25px #00FF7F";
                }, 1000); // Дополнительная задержка для уверенности
              }
            }
          }
        } catch (e) {
          console.error("Ошибка обработки аудио:", e);
        }
      }
    };
    
    // Подключаем процессор к источнику и к выходу
    micSource.connect(scriptProcessor);
    scriptProcessor.connect(audioContext.destination);
    
    micEnabled = true;
    updateStatus("Микрофон активен, говорите...");
    log("🎤 Микрофон запущен и готов к записи");
    
  } catch (error) {
    log(`❌ Ошибка доступа к микрофону: ${error.message}`);
    showError(`Нет доступа к микрофону: ${error.message}`);
    updateStatus("Ошибка микрофона");
  }
}

function stopMicrophone() {
  if (!micEnabled) return;
  
  if (scriptProcessor) {
    try {
      scriptProcessor.disconnect();
    } catch (e) {
      console.error("Ошибка при отключении процессора:", e);
    }
    scriptProcessor = null;
  }
  
  if (micStream) {
    try {
      micStream.getTracks().forEach(track => track.stop());
    } catch (e) {
      console.error("Ошибка при остановке треков микрофона:", e);
    }
    micStream = null;
  }
  
  if (audioContext) {
    try {
      audioContext.close();
    } catch (e) {
      console.error("Ошибка при закрытии audioContext:", e);
    }
    audioContext = null;
  }
  
  micEnabled = false;
  setMicrophoneListening(false);
  updateStatus("Микрофон выключен");
  log("🎤 Микрофон остановлен");
}

// Установка состояния микрофона (визуальная индикация)
function setMicrophoneListening(listening) {
  isListening = listening;
  const micPulseElement = document.getElementById('micPulse');
  
  if (listening) {
    micPulseElement.classList.add('pulsing');
    micPulseElement.classList.add('listening');
  } else {
    micPulseElement.classList.remove('pulsing');
    micPulseElement.classList.remove('listening');
  }
}

// ================ TTS функционал ================
async function playTextAsTTS(text) {
  try {
    // Если текст пустой, не делаем запрос
    if (!text || text.trim() === "") {
      log("⚠️ Пустой текст для TTS, пропускаем");
      return;
    }
    
    // Проверяем, выбран ли браузерный TTS
    const useBrowserTTS = document.getElementById('browserTtsToggle')?.checked || false;
    
    if (useBrowserTTS) {
      // Используем браузерный синтез речи
      log("🔊 Используем браузерный синтез речи...");
      updateStatus("Синтез речи браузером...");
      
      if (!('speechSynthesis' in window)) {
        throw new Error("Браузерный синтез речи не поддерживается");
      }
      
      // Сначала отменяем все предыдущие высказывания
      window.speechSynthesis.cancel();
      
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = 'ru-RU';
      utterance.rate = 0.9;
      utterance.pitch = 1.0;
      
      // Обработчики событий
      utterance.onstart = () => {
        log("🔊 Начало воспроизведения браузерного TTS");
        updateStatus("Jarvis говорит...");
      };
      
      utterance.onend = () => {
        log("🔊 Браузерный TTS завершен");
        updateStatus("Готов к следующему запросу");
      };
      
      utterance.onerror = (e) => {
        log(`❌ Ошибка браузерного TTS: ${e.error}`);
        updateStatus("Ошибка воспроизведения");
      };
      
      // Запускаем синтез
      window.speechSynthesis.speak(utterance);
      return;
    }
    
    log("🔊 Запрос TTS...");
    updateStatus("Синтез речи...");
    
    // Создаем URL для прямого аудиопотока
    const ttsUrl = `${SERVER_URL}/tts_stream`;
    
    // Делаем POST запрос
    const response = await fetch(ttsUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        text: text,
        voice: document.getElementById('voiceSelect').value
      })
    });
    
    if (!response.ok) {
      throw new Error(`TTS ошибка: ${response.status}`);
    }

    // Создаем blob из потока
    const blob = await response.blob();
    const audioUrl = URL.createObjectURL(blob);
    
    // Получаем или создаем аудио элемент
    let audioElement = ensureAudioElement();
    
    // Убеждаемся, что предыдущее аудио остановлено
    try {
      audioElement.pause();
      audioElement.currentTime = 0;
    } catch (e) {
      log(`⚠️ Предупреждение при сбросе аудио: ${e.message}`);
    }
    
    audioElement.src = audioUrl;
    audioElement.onended = () => {
      updateStatus("Готов к следующему запросу");
      log("🔊 Воспроизведение завершено");
      
      // Освобождаем ресурсы
      URL.revokeObjectURL(audioUrl);
    };
    
    // Обработка ошибок воспроизведения
    audioElement.onerror = (e) => {
      log(`❌ Ошибка воспроизведения аудио: ${e}`);
      updateStatus("Ошибка воспроизведения");
      
      // При ошибке воспроизведения пробуем браузерный TTS
      useBrowserTTSFallback(text);
    };
    
    // Воспроизводим аудио после загрузки
    audioElement.onloadeddata = () => {
      log(`🔊 Аудио загружено, длительность: ${audioElement.duration.toFixed(1)}с`);
      audioElement.play()
        .then(() => {
          log("🔊 Воспроизведение начато");
          updateStatus("Jarvis говорит...");
        })
        .catch(e => {
          log(`❌ Ошибка запуска воспроизведения: ${e}`);
          
          // При ошибке воспроизведения пробуем браузерный TTS
          useBrowserTTSFallback(text);
        });
    };
    
  } catch (error) {
    log(`❌ Ошибка TTS: ${error.message}`);
    showError(`Ошибка синтеза речи: ${error.message}`);
    updateStatus("Ошибка синтеза речи");
    
    // Пробуем браузерный TTS как запасной вариант
    useBrowserTTSFallback(text);
    
    // При ошибке TTS, активируем снова микрофон после короткой паузы
    setTimeout(() => {
      updateStatus("Готов к следующему запросу");
    }, 2000);
  }
}

// Функция для использования браузерного TTS при ошибке основного
function useBrowserTTSFallback(text) {
  try {
    log("🔄 Попытка использовать браузерный синтез речи...");
    
    if (!('speechSynthesis' in window)) {
      log("⚠️ Браузерный синтез речи не поддерживается");
      return;
    }
    
    // Сначала отменяем все предыдущие высказывания
    window.speechSynthesis.cancel();
    
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'ru-RU';
    utterance.rate = 0.9;
    utterance.pitch = 1.0;
    
    utterance.onstart = () => {
      log("🔊 Начало воспроизведения браузерного TTS");
    };
    
    utterance.onend = () => {
      log("🔊 Браузерный синтез речи завершен");
      updateStatus("Готов к следующему запросу");
    };
    
    utterance.onerror = (e) => {
      log(`❌ Ошибка браузерного TTS: ${e.error || 'неизвестная ошибка'}`);
    };
    
    // Запускаем синтез
    window.speechSynthesis.speak(utterance);
  } catch (e) {
    log(`❌ Ошибка браузерного синтеза речи: ${e.message}`);
  }
}

// ================ Инициализация ================
document.addEventListener('DOMContentLoaded', function() {
  // Сразу проверяем аудио-элемент
  ensureAudioElement();
  
  // Добавляем обработчик для кнопки Start/Stop
  document.getElementById('startBtn').addEventListener('click', async () => {
    try {
      const startBtn = document.getElementById('startBtn');
      
      if (micEnabled) {
        // Если микрофон активен, останавливаем всё
        stopMicrophone();
        if (ws && ws.readyState !== WebSocket.CLOSED) {
          ws.close();
        }
        startBtn.textContent = "▶️ Начать";
        updateStatus("Остановлено");
      } else {
        // Запускаем сессию и WebSocket
        startBtn.textContent = "⏹️ Остановить";
        startBtn.disabled = true; // Блокируем кнопку во время подключения
        
        try {
          const session = await createSession();
          ws = connectToProxy(session);
          reconnectAttempts = 0; // Сбрасываем счётчик переподключений
        } catch (error) {
          startBtn.textContent = "▶️ Начать";
          showError(error.message);
        } finally {
          startBtn.disabled = false;
        }
      }
    } catch (error) {
      log(`❌ Ошибка: ${error.message}`);
      showError(error.message);
      document.getElementById('startBtn').disabled = false;
      document.getElementById('startBtn').textContent = "▶️ Начать";
    }
  });
  
  // Обработчик для микрофонного пульса
  document.getElementById('micPulse').addEventListener('click', function() {
    // Если сессия активна, но микрофон не активен, запускаем его
    if (ws && ws.readyState === WebSocket.OPEN && !isListening) {
      if (!micEnabled) {
        startMicrophone();
      } else {
        log("🎤 Микрофон уже активен");
        updateStatus("Говорите...");
      }
    } else if (!ws || ws.readyState !== WebSocket.OPEN) {
      showError("Сначала запустите сессию, нажав кнопку 'Начать'");
    }
  });
  
  // Обработчик для браузерного TTS переключателя
  document.getElementById('browserTtsToggle').addEventListener('change', function() {
    const useBrowserTts = this.checked;
    if (useBrowserTts) {
      log("🔊 Включен браузерный синтез речи");
      // Проверяем поддержку
      if (!('speechSynthesis' in window)) {
        log("⚠️ Браузерный синтез речи не поддерживается");
        showError("Ваш браузер не поддерживает синтез речи");
        this.checked = false;
      }
    } else {
      log("🔊 Включен синтез речи через OpenAI API");
    }
  });
  
  // Проверка API
  fetch(`${SERVER_URL}/health`)
    .then(response => response.text())
    .then(data => {
      try {
        const jsonData = JSON.parse(data);
        log(`✅ API готов: ${jsonData.status}`);
        log(`📆 Версия: ${jsonData.version}`);
        updateStatus("Готов к работе");
      } catch (e) {
        log(`⚠️ API вернул не JSON-ответ, но сервер работает`);
      }
    })
    .catch(error => {
      log(`❌ Ошибка проверки API: ${error.message}`);
      showError("Сервер недоступен. Проверьте соединение.");
    });
});

log("✅ Jarvis v5.5 загружен!");
