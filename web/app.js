const workerCode = `
  self.onmessage = function(e) {
    const { action, data } = e.data;
    
    if (action === 'analyzeAudio') {
      const { audioData, bpm } = data;

      let processedSections = 0;
      const totalSections = Math.ceil(audioData.length / 1024);

      const beatTimes = [];
      const frequencies = [];
      let energyThreshold = 0;

      let totalEnergy = 0;
      for (let i = 0; i < audioData.length; i++) {
        totalEnergy += audioData[i] * audioData[i];
      }
      const avgEnergy = totalEnergy / audioData.length;

      const windowSize = Math.floor(44100 / (bpm / 60));
      let peakIndex = 0;
      let maxEnergy = 0;
      
      for (let i = 0; i < audioData.length; i += Math.floor(windowSize / 4)) {
        const end = Math.min(i + windowSize, audioData.length);
        let energy = 0;
        for (let j = i; j < end; j++) {
          energy += audioData[j] * audioData[j];
        }
        energy = energy / windowSize;
        
        if (energy > avgEnergy * 1.2 && energy > maxEnergy) {
          maxEnergy = energy;
          peakIndex = i;
        } else if (i - peakIndex > windowSize * 0.5) {
          if (maxEnergy > avgEnergy * 1.2) {
            beatTimes.push(peakIndex / 44100);
          }
          maxEnergy = 0;
        }

        if (i % (windowSize * 2) === 0) {
          let freq = 100 + Math.random() * 600;
          frequencies.push(freq);
        }
        
        processedSections++;
        if (processedSections % 100 === 0) {
          self.postMessage({
            type: 'progress',
            progress: (processedSections / totalSections) * 100,
            message: \`Analyzing audio... \${Math.round((processedSections / totalSections) * 100)}%\`
          });
        }
      }
      
      self.postMessage({
        type: 'result',
        data: {
          beatTimes,
          frequencies,
          totalBeats: beatTimes.length,
          avgFrequency: frequencies.reduce((a,b) => a + b, 0) / frequencies.length
        }
      });
    }
    
    if (action === 'generateChart') {
      const { playerFiles, opponentFiles, settings } = data;

      try {
        const result = generateChartData(playerFiles, opponentFiles, settings);
        self.postMessage({
          type: 'chartResult',
          data: result
        });
      } catch (error) {
        self.postMessage({
          type: 'error',
          error: error.message
        });
      }
    }
  };
  
  function generateChartData(playerFiles, opponentFiles, settings) {
    const {
      bpm: bpmVal = 180,
      offset: offsetVal = 0,
      defaultSustain: defaultSustainVal = 0,
      preventConsecutive = false,
      maxConsecutive = 1,
      enableSustainAI = false,
      enableTonalAI = false,
      p1 = 'bf',
      p2 = 'dad',
      songName = 'New Song',
      speed = 2.6,
      gameOver = 'bf-dead',
      stage = 'stage',
      gfVer = 'gf'
    } = settings;
    
    const beatMs = 60000 / bpmVal;
    const sectionBeats = 4;
    const sectionMs = beatMs * sectionBeats;
    const songDurationMs = 30000;
    const numSections = Math.max(4, Math.ceil(songDurationMs / sectionMs));
    const stepsPerBeat = 4;
    const stepMs = beatMs / stepsPerBeat;
    
    const PLAYER_NOTES = [0, 1, 2, 3];
    const OPPONENT_NOTES = [4, 5, 6, 7];
    
    const hasPlayer = playerFiles && playerFiles.length > 0;
    const hasOpponent = opponentFiles && opponentFiles.length > 0;
    const generatePlayer = hasPlayer || !hasOpponent;
    const generateOpponent = hasOpponent;
    
    let sections = [];
    let currentTimeMs = 0;
    let mustHitSection = true;
    
    for (let s = 0; s < numSections; s++) {
      self.postMessage({
        type: 'progress',
        progress: 15 + (s / numSections) * 60,
        message: \`Generating section \${s + 1}/\${numSections}\`
      });
      
      let sectionNotes = [];
      const density = Math.min(1, Math.max(0.3, bpmVal / 300));
      const numSteps = Math.floor(sectionMs / stepMs);
      
      let playerNotesInSection = 0;
      let opponentNotesInSection = 0;

      const useAnalyzedData = playerFiles && playerFiles.length > 0 && playerFiles[0].analysisData;
      
      for (let step = 0; step < numSteps; step++) {
        const stepTime = currentTimeMs + step * stepMs + offsetVal;
        let shouldPlaceNote = false;
        
        if (useAnalyzedData && playerFiles[0].analysisData) {
          const beatTimes = playerFiles[0].analysisData.beatTimes || [];
          const beatInterval = beatMs / 1000;
          const normalizedTime = stepTime / 1000;
          
          for (let beat of beatTimes) {
            if (Math.abs(beat - normalizedTime) < beatInterval * 0.2) {
              shouldPlaceNote = true;
              break;
            }
          }
        } else {
          shouldPlaceNote = Math.random() < 0.3 * density;
        }
        
        if (shouldPlaceNote) {
          let isPlayer = false;
          if (generatePlayer && generateOpponent) {
            isPlayer = Math.random() < 0.5;
          } else if (generatePlayer) {
            isPlayer = true;
          }
          
          let direction;
          
          if (enableTonalAI && useAnalyzedData && playerFiles[0].analysisData) {
            const freqs = playerFiles[0].analysisData.frequencies || [];
            const freqIndex = Math.floor(Math.random() * freqs.length);
            const freq = freqs[freqIndex] || 200;
            
            let mapped = 0;
            if (freq < 150) mapped = 0;
            else if (freq < 250) mapped = 1;
            else if (freq < 400) mapped = 2;
            else mapped = 3;
            
            direction = isPlayer ? mapped : mapped + 4;
          } else {
            const notePool = isPlayer ? PLAYER_NOTES : OPPONENT_NOTES;
            direction = notePool[Math.floor(Math.random() * notePool.length)];
          }

          if (preventConsecutive) {
            let lastDirection = null;
            if (sectionNotes.length > 0) {
              lastDirection = sectionNotes[sectionNotes.length - 1][1];
            }
            if (lastDirection !== null && lastDirection === direction) {
              let consecutiveCount = 1;
              for (let i = sectionNotes.length - 1; i >= 0; i--) {
                if (sectionNotes[i][1] === direction) consecutiveCount++;
                else break;
              }
              if (consecutiveCount >= maxConsecutive) {
                const notePool = isPlayer ? PLAYER_NOTES : OPPONENT_NOTES;
                const available = notePool.filter((d) => d !== direction);
                if (available.length > 0) {
                  direction = available[Math.floor(Math.random() * available.length)];
                }
              }
            }
          }
          
          let sustainLength = defaultSustainVal;
          if (enableSustainAI) {
            if (Math.random() < 0.4) {
              const minS = parseInt(settings.minSustain) || 150;
              sustainLength = minS + Math.random() * (500 - minS);
              sustainLength = Math.round(sustainLength / 10) * 10;
            }
          }
          
          sectionNotes.push([stepTime, direction, sustainLength]);
          if (isPlayer) playerNotesInSection++;
          else opponentNotesInSection++;
        }
      }
      
      let mustHit = false;
      if (playerNotesInSection > opponentNotesInSection) mustHit = true;
      else if (opponentNotesInSection > playerNotesInSection) mustHit = false;
      else mustHit = !mustHitSection;
      
      if (generatePlayer && !generateOpponent) mustHit = true;
      if (!generatePlayer && generateOpponent) mustHit = false;
      
      sections.push({
        sectionNotes: sectionNotes,
        sectionBeats: sectionBeats,
        mustHitSection: mustHit,
        typeOfSection: 0,
        altAnim: false,
      });
      
      mustHitSection = mustHit;
      currentTimeMs += sectionMs;
    }
    
    if (sections.length === 0) {
      sections.push({
        sectionNotes: [],
        sectionBeats: 4,
        mustHitSection: true,
        typeOfSection: 0,
        altAnim: false,
      });
    }
    
    return {
      player1: p1,
      player2: p2,
      notes: sections,
      events: [],
      gfVersion: gfVer,
      offset: offsetVal,
      gameOverChar: gameOver,
      song: songName,
      needsVoices: true,
      stage: stage,
      format: "psych_v1_convert",
      bpm: bpmVal,
      speed: parseFloat(speed) || 2.6,
    };
  }
`;

let worker;
let workerInitialized = false;

function createWorker() {
  const blob = new Blob([workerCode], { type: "application/javascript" });
  const workerUrl = URL.createObjectURL(blob);
  return new Worker(workerUrl);
}

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const playerLabel = $("playerFileLabel");
  const opponentLabel = $("opponentFileLabel");
  const loadPlayerBtn = $("loadPlayerBtn");
  const loadOpponentBtn = $("loadOpponentBtn");
  const generateBtn = $("generateBtn");
  const saveBtn = $("saveBtn");
  const cancelBtn = $("cancelBtn");
  const progressFill = $("progressFill");
  const progressMsg = $("progressMsg");
  const progressPct = $("progressPct");
  const statusText = $("statusText");

  const songName = $("songName");
  const bpm = $("bpm");
  const speed = $("speed");
  const p1 = $("p1");
  const p2 = $("p2");
  const gameOver = $("gameOver");
  const stage = $("stage");
  const gfVer = $("gfVer");
  const offset = $("offset");
  const defaultSustain = $("defaultSustain");
  const preventConsecutive = $("preventConsecutive");
  const maxConsecutive = $("maxConsecutive");
  const enableSustain = $("enableSustain");
  const minSustain = $("minSustain");
  const sustainThresh = $("sustainThresh");
  const sustainRelease = $("sustainRelease");
  const sustainExt = $("sustainExt");
  const minSilence = $("minSilence");
  const enableTonality = $("enableTonality");
  const lowC = $("lowC");
  const midC = $("midC");
  const highC = $("highC");
  const confidence = $("confidence");

  let playerFiles = [];
  let opponentFiles = [];
  let chartData = null;
  let isGenerating = false;
  let cancelled = false;
  let currentWorker = null;

  function initWorker() {
    if (!workerInitialized) {
      try {
        currentWorker = createWorker();
        workerInitialized = true;

        currentWorker.onmessage = function (e) {
          const data = e.data;

          if (data.type === "progress") {
            setProgress(data.progress, data.message);
          } else if (data.type === "result") {
            if (playerFiles.length > 0 && data.data) {
              playerFiles[0].analysisData = data.data;
              setStatus(
                `Audio analysis complete: ${Math.round(data.data.totalBeats)} beats detected`,
              );
            }
          } else if (data.type === "chartResult") {
            chartData = data.data;
            setProgress(100, "Complete!");
            setStatus("Chart generated successfully");
            saveBtn.disabled = false;
            isGenerating = false;
            setUIEnabled(true);
            cancelBtn.disabled = true;
            generateBtn.disabled = false;
          } else if (data.type === "error") {
            setStatus("Error: " + data.error);
            setProgress(0, "Error");
            isGenerating = false;
            setUIEnabled(true);
            cancelBtn.disabled = true;
            generateBtn.disabled = false;
          }
        };

        currentWorker.onerror = function (error) {
          setStatus("Worker error: " + error.message);
          isGenerating = false;
          setUIEnabled(true);
          cancelBtn.disabled = true;
          generateBtn.disabled = false;
        };
      } catch (e) {
        console.error("Failed to initialize worker:", e);
        workerInitialized = false;
      }
    }
  }

  function updateFileLabel(el, files, label) {
    if (files.length === 0) {
      el.className = "file-label";
      el.innerHTML = `<span class="material-icons-round">audio_file</span> No ${label} files loaded`;
      el.style.borderStyle = "dashed";
      return;
    }
    el.className = "file-label has-files";
    el.style.borderStyle = "solid";
    const names = files
      .map((f) => f.name || f.split("/").pop() || f)
      .slice(0, 2)
      .join(", ");
    const extra = files.length > 2 ? ` +${files.length - 2}` : "";
    el.innerHTML = `<span class="material-icons-round">check_circle</span> ${files.length} file(s) · ${names}${extra}`;
  }

  function setUIEnabled(enabled) {
    const inputs = [
      songName,
      bpm,
      speed,
      p1,
      p2,
      gameOver,
      stage,
      gfVer,
      offset,
      defaultSustain,
      maxConsecutive,
      minSustain,
      sustainThresh,
      sustainRelease,
      sustainExt,
      minSilence,
      lowC,
      midC,
      highC,
      confidence,
    ];
    const checks = [preventConsecutive, enableSustain, enableTonality];
    const btns = [loadPlayerBtn, loadOpponentBtn, generateBtn];
    inputs.forEach((i) => (i.disabled = !enabled));
    checks.forEach((c) => (c.disabled = !enabled));
    btns.forEach((b) => (b.disabled = !enabled));
    if (!enabled) saveBtn.disabled = true;
  }

  function setProgress(value, msg) {
    const p = Math.min(100, Math.max(0, value));
    progressFill.style.width = p + "%";
    progressPct.textContent = p + "%";
    if (msg) progressMsg.textContent = msg;
  }

  function setStatus(msg) {
    statusText.textContent = msg;
  }

  async function analyzeAudioFile(file) {
    try {
      const arrayBuffer = await file.arrayBuffer();
      const audioContext = new (
        window.AudioContext || window.webkitAudioContext
      )();
      const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);

      const channelData = audioBuffer.getChannelData(0);

      const chunkSize = Math.floor(channelData.length / 10);
      const analysisData = {
        channelData: channelData.slice(0, chunkSize * 2),
        sampleRate: audioBuffer.sampleRate,
        duration: audioBuffer.duration,
      };

      setProgress(10, "Audio loaded, analyzing...");

      if (workerInitialized && currentWorker) {
        currentWorker.postMessage({
          action: "analyzeAudio",
          data: {
            audioData: analysisData.channelData,
            bpm: parseInt(bpm.value) || 180,
          },
        });
        return analysisData;
      } else {
        setProgress(20, "Analyzing audio (simple mode)...");
        await sleep(200);
        return {
          channelData: analysisData.channelData,
          sampleRate: audioBuffer.sampleRate,
          duration: audioBuffer.duration,
          beatTimes: generateSimpleBeats(
            parseInt(bpm.value) || 180,
            audioBuffer.duration,
          ),
        };
      }
    } catch (error) {
      console.error("Audio analysis failed:", error);
      throw new Error("Failed to analyze audio: " + error.message);
    }
  }

  function generateSimpleBeats(bpm, duration) {
    const beatInterval = 60 / bpm;
    const beats = [];
    for (let t = 0; t < duration; t += beatInterval * 2) {
      beats.push(t + (Math.random() - 0.5) * 0.1);
    }
    return beats;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function startGeneration() {
    if (isGenerating) return;
    if (!playerFiles.length && !opponentFiles.length) {
      setStatus("Load at least one voice file");
      return;
    }

    initWorker();

    isGenerating = true;
    cancelled = false;
    setUIEnabled(false);
    cancelBtn.disabled = false;
    saveBtn.disabled = true;
    generateBtn.disabled = true;
    setProgress(0, "Initializing...");
    setStatus("Preparing audio analysis...");

    try {
      if (playerFiles.length > 0 && !playerFiles[0].analysisData) {
        setProgress(5, "Analyzing player audio...");
        await analyzeAudioFile(playerFiles[0]);

        if (cancelled) throw new Error("Cancelled");
      }

      setProgress(15, "Generating chart...");

      if (cancelled) throw new Error("Cancelled");

      const settings = {
        bpm: parseInt(bpm.value) || 180,
        offset: parseFloat(offset.value) || 0,
        defaultSustain: parseInt(defaultSustain.value) || 0,
        preventConsecutive: preventConsecutive.checked,
        maxConsecutive: parseInt(maxConsecutive.value) || 1,
        enableSustainAI: enableSustain.checked,
        enableTonalAI: enableTonality.checked,
        p1: p1.value || "bf",
        p2: p2.value || "dad",
        songName: songName.value || "New Song",
        speed: parseFloat(speed.value) || 2.6,
        gameOver: gameOver.value || "bf-dead",
        stage: stage.value || "stage",
        gfVer: gfVer.value || "gf",
        minSustain: parseInt(minSustain.value) || 150,
        minSilence: parseInt(minSilence.value) || 50,
      };

      if (workerInitialized && currentWorker) {
        currentWorker.postMessage({
          action: "generateChart",
          data: {
            playerFiles: playerFiles.map((f) => ({
              name: f.name,
              analysisData: f.analysisData || null,
            })),
            opponentFiles: opponentFiles.map((f) => ({
              name: f.name,
              analysisData: f.analysisData || null,
            })),
            settings: settings,
          },
        });

        setStatus("Chart generation in progress...");
      } else {
        const data = await generateChartFallback(settings);
        if (cancelled) throw new Error("Cancelled");

        chartData = data;
        setProgress(100, "Complete!");
        setStatus("Chart generated successfully");
        saveBtn.disabled = false;
        isGenerating = false;
        setUIEnabled(true);
        cancelBtn.disabled = true;
        generateBtn.disabled = false;
      }
    } catch (e) {
      if (e.message === "Cancelled") {
        setStatus("Generation cancelled");
        setProgress(0, "Cancelled");
      } else {
        setStatus("Error: " + e.message);
        setProgress(0, "Error");
        console.error(e);
      }
      isGenerating = false;
      setUIEnabled(true);
      cancelBtn.disabled = true;
      generateBtn.disabled = false;
    }
  }

  async function generateChartFallback(settings) {
    const {
      bpm: bpmVal = 180,
      offset: offsetVal = 0,
      defaultSustain: defaultSustainVal = 0,
      preventConsecutive = false,
      maxConsecutive = 1,
      enableSustainAI = false,
      enableTonalAI = false,
      p1: p1Val = "bf",
      p2: p2Val = "dad",
      songName: songNameVal = "New Song",
      speed: speedVal = 2.6,
      gameOver: gameOverVal = "bf-dead",
      stage: stageVal = "stage",
      gfVer: gfVerVal = "gf",
    } = settings;

    const beatMs = 60000 / bpmVal;
    const sectionBeats = 4;
    const sectionMs = beatMs * sectionBeats;
    const songDurationMs = 30000;
    const numSections = Math.max(4, Math.ceil(songDurationMs / sectionMs));
    const stepsPerBeat = 4;
    const stepMs = beatMs / stepsPerBeat;

    const PLAYER_NOTES = [0, 1, 2, 3];
    const OPPONENT_NOTES = [4, 5, 6, 7];

    const hasPlayer = playerFiles.length > 0;
    const hasOpponent = opponentFiles.length > 0;
    const generatePlayer = hasPlayer || !hasOpponent;
    const generateOpponent = hasOpponent;

    let sections = [];
    let currentTimeMs = 0;
    let mustHitSection = true;

    for (let s = 0; s < numSections; s++) {
      if (cancelled) throw new Error("Cancelled");
      setProgress(
        15 + (s / numSections) * 60,
        `Generating section ${s + 1}/${numSections}`,
      );
      await sleep(30);

      let sectionNotes = [];
      const density = Math.min(1, Math.max(0.3, bpmVal / 300));
      const numSteps = Math.floor(sectionMs / stepMs);

      let playerNotesInSection = 0;
      let opponentNotesInSection = 0;

      for (let step = 0; step < numSteps; step++) {
        if (cancelled) throw new Error("Cancelled");

        let shouldPlaceNote = false;
        if (playerFiles.length > 0 && playerFiles[0].analysisData) {
          const beatTimes = playerFiles[0].analysisData.beatTimes || [];
          const stepTime = (currentTimeMs + step * stepMs + offsetVal) / 1000;
          const beatInterval = beatMs / 1000;

          for (let beat of beatTimes) {
            if (Math.abs(beat - stepTime) < beatInterval * 0.15) {
              shouldPlaceNote = true;
              break;
            }
          }
        } else {
          shouldPlaceNote = Math.random() < 0.3 * density;
        }

        if (shouldPlaceNote) {
          let isPlayer = false;
          if (generatePlayer && generateOpponent) {
            isPlayer = Math.random() < 0.5;
          } else if (generatePlayer) {
            isPlayer = true;
          }

          let direction;
          let notePool = isPlayer ? PLAYER_NOTES : OPPONENT_NOTES;

          if (
            enableTonalAI &&
            playerFiles.length > 0 &&
            playerFiles[0].analysisData
          ) {
            const freqs = playerFiles[0].analysisData.frequencies || [];
            const freqIndex = Math.floor(Math.random() * freqs.length);
            const freq = freqs[freqIndex] || 200;

            let mapped = 0;
            const lowCVal = parseInt(lowC.value) || 150;
            const midCVal = parseInt(midC.value) || 250;
            const highCVal = parseInt(highC.value) || 400;

            if (freq < lowCVal) mapped = 0;
            else if (freq < midCVal) mapped = 1;
            else if (freq < highCVal) mapped = 2;
            else mapped = 3;

            direction = isPlayer ? mapped : mapped + 4;
          } else {
            direction = notePool[Math.floor(Math.random() * notePool.length)];
          }

          if (preventConsecutive) {
            let lastDirection = null;
            if (sectionNotes.length > 0) {
              lastDirection = sectionNotes[sectionNotes.length - 1][1];
            }
            if (lastDirection !== null && lastDirection === direction) {
              let consecutiveCount = 1;
              for (let i = sectionNotes.length - 1; i >= 0; i--) {
                if (sectionNotes[i][1] === direction) consecutiveCount++;
                else break;
              }
              if (consecutiveCount >= maxConsecutive) {
                const available = notePool.filter((d) => d !== direction);
                if (available.length > 0) {
                  direction =
                    available[Math.floor(Math.random() * available.length)];
                }
              }
            }
          }

          const noteTimeMs = currentTimeMs + step * stepMs + offsetVal;

          let sustainLength = defaultSustainVal;
          if (enableSustainAI) {
            if (Math.random() < 0.4) {
              const minS = parseInt(minSustain.value) || 150;
              sustainLength = minS + Math.random() * (500 - minS);
              sustainLength = Math.round(sustainLength / 10) * 10;
            }
          }

          sectionNotes.push([noteTimeMs, direction, sustainLength]);
          if (isPlayer) playerNotesInSection++;
          else opponentNotesInSection++;
        }
      }

      let mustHit = false;
      if (playerNotesInSection > opponentNotesInSection) mustHit = true;
      else if (opponentNotesInSection > playerNotesInSection) mustHit = false;
      else mustHit = !mustHitSection;

      if (generatePlayer && !generateOpponent) mustHit = true;
      if (!generatePlayer && generateOpponent) mustHit = false;

      sections.push({
        sectionNotes: sectionNotes,
        sectionBeats: sectionBeats,
        mustHitSection: mustHit,
        typeOfSection: 0,
        altAnim: false,
      });

      mustHitSection = mustHit;
      currentTimeMs += sectionMs;
    }

    if (sections.length === 0) {
      sections.push({
        sectionNotes: [],
        sectionBeats: 4,
        mustHitSection: true,
        typeOfSection: 0,
        altAnim: false,
      });
    }

    return {
      player1: p1Val,
      player2: p2Val,
      notes: sections,
      events: [],
      gfVersion: gfVerVal,
      offset: offsetVal,
      gameOverChar: gameOverVal,
      song: songNameVal,
      needsVoices: true,
      stage: stageVal,
      format: "psych_v1_convert",
      bpm: bpmVal,
      speed: parseFloat(speedVal) || 2.6,
    };
  }

  function cancelGeneration() {
    if (isGenerating) {
      cancelled = true;
      setStatus("Cancelling...");
      cancelBtn.disabled = true;
    }
  }

  function saveChart() {
    if (!chartData) {
      setStatus("No chart to save");
      return;
    }
    const name = (songName.value || "chart").replace(/\s+/g, "_");
    const json = JSON.stringify(chartData, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${name}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setStatus(`Saved ${name}.json`);
  }

  function setupDragAndDrop() {
    const dropZones = [
      {
        element: playerLabel,
        type: "player",
        files: playerFiles,
        label: playerLabel,
      },
      {
        element: opponentLabel,
        type: "opponent",
        files: opponentFiles,
        label: opponentLabel,
      },
    ];

    dropZones.forEach(({ element, type, label }) => {
      element.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.stopPropagation();
        element.classList.add("drag-over");
        element.style.borderColor = "#66bb6a";
        element.style.background = "rgba(76, 175, 80, 0.1)";
      });

      element.addEventListener("dragleave", (e) => {
        e.preventDefault();
        e.stopPropagation();
        element.classList.remove("drag-over");
        element.style.borderColor = "";
        element.style.background = "";
      });

      element.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();
        element.classList.remove("drag-over");
        element.style.borderColor = "";
        element.style.background = "";

        const files = Array.from(e.dataTransfer.files);
        const oggFiles = files.filter((f) =>
          f.name.toLowerCase().endsWith(".ogg"),
        );

        if (oggFiles.length === 0) {
          setStatus("Please drop .ogg files only");
          return;
        }

        if (type === "player") {
          playerFiles = oggFiles;
          updateFileLabel(playerLabel, playerFiles, "player");
          setStatus(`Loaded ${playerFiles.length} player file(s)`);
        } else {
          opponentFiles = oggFiles;
          updateFileLabel(opponentLabel, opponentFiles, "opponent");
          setStatus(`Loaded ${opponentFiles.length} opponent file(s)`);
        }
      });
    });

    document.querySelectorAll(".m3-card").forEach((card) => {
      card.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.stopPropagation();
      });

      card.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();

        const files = Array.from(e.dataTransfer.files);
        const oggFiles = files.filter((f) =>
          f.name.toLowerCase().endsWith(".ogg"),
        );

        if (oggFiles.length === 0) {
          setStatus("Please drop .ogg files only");
          return;
        }

        const cardRect = card.getBoundingClientRect();
        const mouseY = e.clientY - cardRect.top;
        const halfway = cardRect.height / 2;

        if (mouseY < halfway) {
          playerFiles = oggFiles;
          updateFileLabel(playerLabel, playerFiles, "player");
          setStatus(`Loaded ${playerFiles.length} player file(s)`);
        } else {
          opponentFiles = oggFiles;
          updateFileLabel(opponentLabel, opponentFiles, "opponent");
          setStatus(`Loaded ${opponentFiles.length} opponent file(s)`);
        }
      });
    });
  }

  function loadFiles(type) {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".ogg";
    input.multiple = true;
    input.onchange = (e) => {
      const files = Array.from(e.target.files);
      if (files.length === 0) return;

      if (type === "player") {
        playerFiles = files;
        updateFileLabel(playerLabel, playerFiles, "player");
        setStatus(`Loaded ${playerFiles.length} player file(s)`);
      } else {
        opponentFiles = files;
        updateFileLabel(opponentLabel, opponentFiles, "opponent");
        setStatus(`Loaded ${opponentFiles.length} opponent file(s)`);
      }
    };
    input.click();
  }

  document.querySelectorAll(".m3-tab").forEach((tab) => {
    tab.addEventListener("click", function () {
      document
        .querySelectorAll(".m3-tab")
        .forEach((t) => t.classList.remove("active"));
      this.classList.add("active");
      const target = this.dataset.tab;
      document
        .querySelectorAll(".tab-panel")
        .forEach((p) => p.classList.remove("active"));
      document.getElementById("panel-" + target).classList.add("active");
    });
  });

  loadPlayerBtn.addEventListener("click", () => loadFiles("player"));
  loadOpponentBtn.addEventListener("click", () => loadFiles("opponent"));
  generateBtn.addEventListener("click", startGeneration);
  cancelBtn.addEventListener("click", cancelGeneration);
  saveBtn.addEventListener("click", saveChart);

  function toggleSustain() {
    const checked = enableSustain.checked;
    [minSustain, sustainThresh, sustainRelease, sustainExt, minSilence].forEach(
      (el) => (el.disabled = !checked),
    );
  }

  function toggleTonality() {
    const checked = enableTonality.checked;
    [lowC, midC, highC, confidence].forEach((el) => (el.disabled = !checked));
  }

  enableSustain.addEventListener("change", toggleSustain);
  enableTonality.addEventListener("change", toggleTonality);
  toggleSustain();
  toggleTonality();

  setupDragAndDrop();

  updateFileLabel(playerLabel, [], "player");
  updateFileLabel(opponentLabel, [], "opponent");
  setProgress(0, "Ready");
  setStatus("Load audio & generate (drag .ogg files)");
})();
