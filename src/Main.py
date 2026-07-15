import sys
import time
import os
import json
import random
import numpy as np
from utils import resources
from collections import OrderedDict

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QTabWidget,
    QSizePolicy,
    QSplashScreen,
)
from PySide6.QtCore import (
    Qt,
    Signal,
    QThread,
    QTimer,
    QMutex,
)
from PySide6.QtGui import (
    QIcon,
    QPainter,
    QPixmap,
)

import librosa
import qtawesome as qta

SAMPLE_RATE = 44100
PLAYER_NOTES = [0, 1, 2, 3]
OPPONENT_NOTES = [4, 5, 6, 7]

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

class AudioLoadThread(QThread):
    loading_complete = Signal(object, bool)
    loading_error = Signal(str, bool)

    def __init__(self, files, is_player):
        super().__init__()
        self.files = files
        self.is_player = is_player

    def run(self):
        try:
            combined = np.array([])
            loaded_files = []
            
            for file_path in self.files:
                try:
                    y, sr = librosa.load(file_path, sr=SAMPLE_RATE)
                    if len(y) == 0:
                        print(f"Warning: {file_path} is empty, skipping")
                        continue
                    combined = np.concatenate((combined, y))
                    loaded_files.append(file_path)
                except Exception as e:
                    print(f"Error loading {file_path}: {e}")
                    continue

            if len(combined) == 0:
                self.loading_complete.emit(None, self.is_player)
            else:
                self.loading_complete.emit((combined, loaded_files), self.is_player)
                
        except Exception as e:
            self.loading_error.emit(str(e), self.is_player)


class ChartGenerationThread(QThread):
    progress_updated = Signal(int, str)
    generation_complete = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self, generator, parent=None):
        super().__init__(parent)
        self.generator = generator
        self._is_cancelled = False
        self.mutex = QMutex()

    def cancel(self):
        self.mutex.lock()
        self._is_cancelled = True
        self.mutex.unlock()

    def is_cancelled(self):
        self.mutex.lock()
        cancelled = self._is_cancelled
        self.mutex.unlock()
        return cancelled

    def run(self):
        try:
            chart_data = self.generator.generate_chart(cancellation_check=lambda: self.is_cancelled())
            
            if self.is_cancelled():
                return
                
            self.generation_complete.emit(chart_data)
        except Exception as e:
            if not self.is_cancelled():
                self.error_occurred.emit(str(e))


class SafeProgressUpdater:
    def __init__(self, callback):
        self.callback = callback
        self.mutex = QMutex()
        self.last_value = -1
        self.last_message = ""

    def update(self, value, message=""):
        self.mutex.lock()
        try:
            if abs(value - self.last_value) >= 1 or message != self.last_message:
                self.last_value = value
                self.last_message = message
                if self.callback:
                    QTimer.singleShot(0, lambda: self.callback(value, message))
        finally:
            self.mutex.unlock()


class FNFChartGenerator:
    def __init__(self, progress_callback=None):
        self.player_voices = None
        self.opponent_voices = None
        self.player_voice_files = []
        self.opponent_voice_files = []

        self.bpm = 180
        self.song_name = "New Song"
        self.player1_char = "bf"
        self.player2_char = "dad"
        self.speed = 2.6
        self.default_sustain_length = 0
        self.offset = 0.0
        self.gf_version = "gf"
        self.game_over_char = "bf-dead"
        self.stage = "stage"

        self.prevent_consecutive_notes = False
        self.max_consecutive_allowed_count = 1

        self.onset_delta = 0.03
        self.onset_wait = 30
        self.onset_backtrack = True

        self.enable_ai_sustain = False
        self.min_sustain_duration_ms = 150
        self.sustain_threshold_db = -30.0
        self.sustain_release_threshold_db = -28.0
        self.sustain_extension_ms = 150
        self.min_silence_between_sustains_ms = 50

        self.enable_ai_tonality = False
        self.pitch_low_c_threshold = 150.0
        self.pitch_mid_c_threshold = 250.0
        self.pitch_high_c_threshold = 400.0
        self.pitch_very_high_c_threshold = 600.0
        self.pitch_confidence_threshold = 0.8

        self.min_note_duration_ms = 20
        self.quantization_subdivision = 16

        self.progress_updater = SafeProgressUpdater(progress_callback)
        self.progress_callback = self.progress_updater.update

    def update_progress(self, value, message=""):
        self.progress_callback(value, message)

    def load_audio_files(self, audio_data, files, is_player=True):
        if audio_data is None or len(audio_data) == 0:
            if is_player:
                self.player_voices = None
                self.player_voice_files = []
            else:
                self.opponent_voices = None
                self.opponent_voice_files = []
            return 0
        
        if is_player:
            self.player_voices = audio_data
            self.player_voice_files = files
        else:
            self.opponent_voices = audio_data
            self.opponent_voice_files = files
        
        return len(files)

    def _find_onsets(self, y):
        if y is None or len(y) == 0:
            return []

        self.update_progress(75, "Detecting notes (onsets)...")
        wait_seconds = self.onset_wait / 1000.0 if self.onset_wait > 0 else None

        try:
            return librosa.onset.onset_detect(
                y=y,
                sr=SAMPLE_RATE,
                units="time",
                delta=self.onset_delta,
                wait=wait_seconds,
                backtrack=self.onset_backtrack,
            )
        except Exception as e:
            print(f"Onset detection error: {e}")
            return []

    def _detect_sustain_segments(self, y):
        if y is None or len(y) == 0:
            return []

        try:
            frame_length = 2048
            hop_length = 512
            rms = librosa.feature.rms(
                y=y, frame_length=frame_length, hop_length=hop_length
            )[0]
            rms_db = librosa.amplitude_to_db(rms, ref=np.max)

            sustain_segments = []
            in_sustain = False
            segment_start_frame = 0

            for i in range(len(rms_db)):
                current_rms_db = rms_db[i]
                if not in_sustain:
                    if current_rms_db > self.sustain_threshold_db:
                        segment_start_frame = i
                        in_sustain = True
                else:
                    if current_rms_db < self.sustain_release_threshold_db:
                        segment_end_frame = i

                        start_time_ms = (
                            librosa.frames_to_time(
                                segment_start_frame,
                                sr=SAMPLE_RATE,
                                hop_length=hop_length,
                            )
                            * 1000
                        )
                        end_time_ms = (
                            librosa.frames_to_time(
                                segment_end_frame, sr=SAMPLE_RATE, hop_length=hop_length
                            )
                            * 1000
                        )

                        duration_ms = end_time_ms - start_time_ms

                        if duration_ms >= self.min_sustain_duration_ms:
                            sustain_segments.append((start_time_ms, end_time_ms))

                        in_sustain = False

            if in_sustain:
                start_time_ms = (
                    librosa.frames_to_time(
                        segment_start_frame, sr=SAMPLE_RATE, hop_length=hop_length
                    )
                    * 1000
                )
                end_time_ms = (
                    librosa.frames_to_time(
                        len(rms_db) - 1, sr=SAMPLE_RATE, hop_length=hop_length
                    )
                    * 1000
                )
                duration_ms = end_time_ms - start_time_ms
                if duration_ms >= self.min_sustain_duration_ms:
                    sustain_segments.append((start_time_ms, end_time_ms))

            if self.min_silence_between_sustains_ms > 0:
                merged_segments = []
                if sustain_segments:
                    current_segment = list(sustain_segments[0])
                    for i in range(1, len(sustain_segments)):
                        next_segment = sustain_segments[i]
                        gap_duration = next_segment[0] - current_segment[1]

                        if gap_duration < self.min_silence_between_sustains_ms:
                            current_segment[1] = next_segment[1]
                        else:
                            merged_segments.append(tuple(current_segment))
                            current_segment = list(next_segment)
                    merged_segments.append(tuple(current_segment))
                sustain_segments = merged_segments

            return sustain_segments
        except Exception as e:
            print(f"Sustain detection error: {e}")
            return []

    def _detect_pitch_for_notes(self, y, onsets, is_player):
        pitched_notes = {}
        if y is None or len(y) == 0:
            return pitched_notes

        base_note_offset = 0 if is_player else 4

        for onset_time_sec in onsets:
            ms_time = round(onset_time_sec * 1000, 3)

            start_sample = int(max(0, onset_time_sec - 0.1) * SAMPLE_RATE)
            end_sample = int(
                min(len(y) / SAMPLE_RATE, onset_time_sec + 0.1) * SAMPLE_RATE
            )

            segment = y[start_sample:end_sample]

            if len(segment) == 0:
                continue

            try:
                f0, confidence, _ = librosa.pyin(
                    y=segment,
                    sr=SAMPLE_RATE,
                    fmin=librosa.note_to_hz("C2"),
                    fmax=librosa.note_to_hz("C7"),
                )

                valid_f0_indices = ~np.isnan(f0) & (
                    confidence > self.pitch_confidence_threshold
                )

                if np.any(valid_f0_indices):
                    dominant_f0 = np.median(f0[valid_f0_indices])

                    assigned_note = -1
                    if dominant_f0 < self.pitch_low_c_threshold:
                        assigned_note = 0 + base_note_offset
                    elif dominant_f0 < self.pitch_mid_c_threshold:
                        assigned_note = 1 + base_note_offset
                    elif dominant_f0 < self.pitch_high_c_threshold:
                        assigned_note = 2 + base_note_offset
                    else:
                        assigned_note = 3 + base_note_offset

                    if assigned_note != -1:
                        pitched_notes[ms_time] = assigned_note
            except Exception as e:
                print(f"Pitch detection error at {onset_time_sec}s: {e}")
                continue

        return pitched_notes

    def generate_chart(self, cancellation_check=None):
        try:
            if self.player_voices is None and self.opponent_voices is None:
                raise ValueError(
                    "No voices loaded - load player or opponent voices to generate a chart."
                )

            if cancellation_check and cancellation_check():
                return None

            self.update_progress(5, "Starting chart generation")

            if self.player_voices is not None and len(self.player_voices) == 0:
                self.update_progress(10, "Player voices loaded but empty")
                player_onsets = []
            else:
                player_onsets = (
                    self._find_onsets(self.player_voices)
                    if self.player_voices is not None
                    else []
                )

            if cancellation_check and cancellation_check():
                return None

            if self.opponent_voices is not None and len(self.opponent_voices) == 0:
                self.update_progress(15, "Opponent voices loaded but empty")
                opponent_onsets = []
            else:
                opponent_onsets = (
                    self._find_onsets(self.opponent_voices)
                    if self.opponent_voices is not None
                    else []
                )

            self.update_progress(
                25,
                f"Found {len(player_onsets)} player onsets and {len(opponent_onsets)} opponent onsets",
            )

            if cancellation_check and cancellation_check():
                return None

            self.update_progress(30, "Combining and sorting notes...")

            combined_onsets = [(t, True) for t in player_onsets] + [
                (t, False) for t in opponent_onsets
            ]
            combined_onsets.sort(key=lambda x: x[0])

            player_sustain_segments = []
            opponent_sustain_segments = []
            if self.enable_ai_sustain:
                self.update_progress(35, "Detecting sustain segments...")
                player_sustain_segments = self._detect_sustain_segments(
                    self.player_voices
                )
                opponent_sustain_segments = self._detect_sustain_segments(
                    self.opponent_voices
                )

            if cancellation_check and cancellation_check():
                return None

            player_pitched_notes = {}
            opponent_pitched_notes = {}
            if self.enable_ai_tonality:
                self.update_progress(40, "Analyzing tonality...")
                player_pitched_notes = self._detect_pitch_for_notes(
                    self.player_voices, player_onsets, True
                )
                opponent_pitched_notes = self._detect_pitch_for_notes(
                    self.opponent_voices, opponent_onsets, False
                )

            if cancellation_check and cancellation_check():
                return None

            sections = []
            beat_duration_ms = 60000 / self.bpm
            section_duration_ms = beat_duration_ms * 4
            current_section_start_ms = 0.0
            onset_index = 0
            current_must_hit_state = False

            max_song_time_ms = 0
            if combined_onsets:
                max_song_time_ms = combined_onsets[-1][0] * 1000

            self.update_progress(50, "Building chart sections...")

            while current_section_start_ms <= max_song_time_ms + section_duration_ms:
                if cancellation_check and cancellation_check():
                    return None
                    
                section_notes = []
                num_player_notes = 0
                num_opponent_notes = 0

                while onset_index < len(combined_onsets):
                    onset_time_sec, is_player_note = combined_onsets[onset_index]
                    ms_time = round(onset_time_sec * 1000, 3)

                    if ms_time < current_section_start_ms + section_duration_ms:
                        note_pool = PLAYER_NOTES if is_player_note else OPPONENT_NOTES
                        chosen_direction = random.choice(note_pool)

                        if self.enable_ai_tonality:
                            pitched_notes_map = (
                                player_pitched_notes
                                if is_player_note
                                else opponent_pitched_notes
                            )
                            if ms_time in pitched_notes_map:
                                chosen_direction = pitched_notes_map[ms_time]
                                if chosen_direction not in note_pool:
                                    chosen_direction = random.choice(note_pool)

                        if self.prevent_consecutive_notes:
                            max_allowed = self.max_consecutive_allowed_count
                            is_problematic = False

                            if max_allowed == 0:
                                if (
                                    len(section_notes) > 0
                                    and section_notes[-1][1] == chosen_direction
                                ):
                                    is_problematic = True
                            else:
                                if len(section_notes) >= max_allowed:
                                    last_notes = [
                                        note[1] for note in section_notes[-max_allowed:]
                                    ]
                                    if all(n == chosen_direction for n in last_notes):
                                        is_problematic = True

                            if is_problematic:
                                available_directions = [
                                    d for d in note_pool if d != chosen_direction
                                ]
                                if available_directions:
                                    chosen_direction = random.choice(
                                        available_directions
                                    )

                        note_sustain_length = self.default_sustain_length

                        if self.enable_ai_sustain:
                            sustain_segments = (
                                player_sustain_segments
                                if is_player_note
                                else opponent_sustain_segments
                            )
                            for seg_start, seg_end in sustain_segments:
                                if seg_start <= ms_time < seg_end:
                                    calculated_sustain_end = (
                                        seg_end + self.sustain_extension_ms
                                    )
                                    sustain_duration = calculated_sustain_end - ms_time

                                    next_onset_time = float("inf")
                                    for next_idx in range(
                                        onset_index + 1, len(combined_onsets)
                                    ):
                                        next_onset_time = round(
                                            combined_onsets[next_idx][0] * 1000, 3
                                        )
                                        break

                                    if ms_time + sustain_duration > next_onset_time:
                                        sustain_duration = next_onset_time - ms_time - 1

                                    if sustain_duration > self.min_sustain_duration_ms:
                                        note_sustain_length = round(sustain_duration, 3)
                                    break

                        section_notes.append(
                            [ms_time, chosen_direction, note_sustain_length]
                        )

                        if is_player_note:
                            num_player_notes += 1
                        else:
                            num_opponent_notes += 1

                        onset_index += 1
                    else:
                        break

                must_hit_section = False
                if num_player_notes > num_opponent_notes:
                    must_hit_section = True
                elif num_opponent_notes > num_player_notes:
                    must_hit_section = False
                else:
                    must_hit_section = not current_must_hit_state

                sections.append(
                    {
                        "sectionNotes": section_notes,
                        "sectionBeats": 4,
                        "mustHitSection": must_hit_section,
                        "typeOfSection": 0,
                        "altAnim": False,
                    }
                )

                current_must_hit_state = must_hit_section
                current_section_start_ms += section_duration_ms

                if (
                    onset_index >= len(combined_onsets)
                    and current_section_start_ms
                    > max_song_time_ms + section_duration_ms
                ):
                    break
                if not combined_onsets and len(sections) >= 1:
                    break

            if not sections:
                sections.append(
                    {
                        "sectionNotes": [],
                        "sectionBeats": 4,
                        "mustHitSection": True,
                        "typeOfSection": 0,
                        "altAnim": False,
                    }
                )

            self.update_progress(100, "Constructing final chart JSON...")

            return OrderedDict(
                [
                    ("player1", self.player1_char),
                    ("player2", self.player2_char),
                    ("notes", sections),
                    ("events", []),
                    ("gfVersion", self.gf_version),
                    ("offset", self.offset),
                    ("gameOverChar", self.game_over_char),
                    ("song", self.song_name),
                    ("needsVoices", True),
                    ("stage", self.stage),
                    ("format", "psych_v1_convert"),
                    ("bpm", self.bpm),
                    ("speed", self.speed),
                ]
            )

        except Exception as e:
            self.update_progress(0, f"Error during generation: {e}")
            raise


class ModernGroupBox(QGroupBox):
    def __init__(self, title, parent=None, icon=None):
        super().__init__(title, parent)
        self.icon = icon
        self.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid rgba(69, 99, 69, 0.4);
                border-radius: 12px;
                margin-top: 12px;
                padding-top: 10px;
                background-color: transparent;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 15px;
                padding: 0 10px 0 10px;
                color: #9bcd6c;
                font-size: 13px;
                letter-spacing: 0.5px;
            }
        """)
    
    def paintEvent(self, event):
        super().paintEvent(event)
        if self.icon:
            painter = QPainter(self)
            icon = qta.icon(self.icon, color='#9bcd6c')
            pixmap = icon.pixmap(16, 16)
            painter.drawPixmap(10, 12, pixmap)


class IconButton(QPushButton):
    def __init__(self, text, icon_name, icon_color='#e8f5e9', parent=None):
        super().__init__(text, parent)
        self.icon_name = icon_name
        self.icon_color = icon_color
        self.update_icon()
        self.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding-left: 40px;
            }
        """)
    
    def update_icon(self):
        icon = qta.icon(self.icon_name, color=self.icon_color)
        self.setIcon(icon)
        self.setIconSize(self.iconSize())


class ChartGeneratorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.chart_generator = FNFChartGenerator(self.update_progress)
        self.current_chart_data = None
        self.generation_thread = None
        self.audio_load_thread = None
        self._pending_update = None

        self.init_ui()
        self.load_settings()
        self.center_window()
        self.show()

    def center_window(self):
        screen_geometry = QApplication.primaryScreen().availableGeometry()
        screen_center = screen_geometry.center()

        window_geometry = self.frameGeometry()
        window_geometry.moveCenter(screen_center)

        self.move(window_geometry.topLeft())

    def init_ui(self):
        self.resize(1200, 700)
        self.setMinimumSize(900, 500)

        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0a1f0a, stop:0.5 #0d2b0d, stop:1 #0a1f0a);
            }
            QLabel {
                color: #c8e6c9;
                font-size: 13px;
            }
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2e5c2e, stop:1 #1e3a1e);
                color: #e8f5e9;
                border: none;
                padding: 10px 20px;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3e7c3e, stop:1 #2e5c2e);
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1e3a1e, stop:1 #2e5c2e);
            }
            QPushButton:disabled {
                background: #2e5c2e;
                color: #7f8c8d;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox {
                padding: 8px 12px;
                border: 1px solid rgba(69, 99, 69, 0.6);
                border-radius: 8px;
                background-color: rgba(15, 25, 15, 0.8);
                color: #d4e6d4;
                font-size: 13px;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #6abf4b;
                background-color: rgba(20, 35, 20, 0.9);
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button {
                background-color: rgba(69, 109, 69, 0.8);
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 25px;
                height: 15px;
                border-top-right-radius: 6px;
                border: none;
            }
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                background-color: rgba(69, 109, 69, 0.8);
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 25px;
                height: 15px;
                border-bottom-right-radius: 6px;
                border: none;
            }
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: rgba(89, 129, 89, 0.9);
            }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
                image: url(:arrows/ui/arrow-up.png);
                margin: 0 auto;
            }
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                image: url(:arrows/ui/arrow-down.png);
                margin: 0 auto;
            }
            QProgressBar {
                border: none;
                border-radius: 10px;
                text-align: center;
                color: #e8f5e9;
                background-color: rgba(30, 50, 30, 0.5);
                height: 25px;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4caf50, stop:1 #2e7d32);
                border-radius: 10px;
            }
            QTabWidget::pane {
                border: none;
                background-color: transparent;
            }
            QTabBar::tab {
                background-color: rgba(25, 45, 25, 0.6);
                color: #9bcd6c;
                padding: 10px 24px;
                margin-right: 4px;
                border: none;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                font-size: 13px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: rgba(40, 60, 40, 0.9);
                color: #81c784;
                border-bottom: 2px solid #4caf50;
            }
            QTabBar::tab:hover:!selected {
                background-color: rgba(50, 70, 50, 0.7);
                color: #a5d6a7;
            }
            QTextEdit {
                background-color: rgba(10, 20, 10, 0.8);
                color: #d4e6d4;
                border: 1px solid rgba(69, 99, 69, 0.4);
                border-radius: 8px;
                font-family: monospace;
                padding: 8px;
            }
            QCheckBox {
                color: #c8e6c9;
                font-size: 13px;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid rgba(69, 99, 69, 0.6);
                background-color: rgba(15, 25, 15, 0.8);
            }
            QCheckBox::indicator:checked {
                background-color: #4caf50;
                border: 1px solid #4caf50;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background-color: rgba(30, 50, 30, 0.5);
                width: 10px;
                border-radius: 5px;
            }
            QScrollBar::handle:vertical {
                background-color: rgba(69, 109, 69, 0.8);
                border-radius: 5px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: rgba(89, 129, 89, 0.9);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(15)

        header_widget = QWidget()
        header_widget.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(76, 175, 80, 0.1), stop:1 rgba(46, 125, 50, 0.05));
            border-radius: 15px;
            padding: 10px;
        """)
        header_layout = QHBoxLayout(header_widget)
        header_layout.addStretch()

        header_icon = qta.icon('fa5s.music', color='#81c784')
        icon_label = QLabel()
        icon_label.setPixmap(header_icon.pixmap(32, 32))
        
        title_label = QLabel("FNF Chart Generator Redux")
        title_label.setStyleSheet("""
            font-size: 24px;
            font-weight: bold;
            color: #81c784;
            letter-spacing: 1px;
        """)

        header_text_layout = QVBoxLayout()
        header_text_layout.addWidget(title_label)

        header_layout.addWidget(icon_label)
        header_layout.addSpacing(10)
        header_layout.addLayout(header_text_layout)
        header_layout.addStretch()

        main_layout.addWidget(header_widget)

        tab_widget = QTabWidget()
        main_layout.addWidget(tab_widget)

        self.basic_tab = self.create_basic_tab()
        self.advanced_tab = self.create_advanced_tab()

        tab_widget.addTab(self.basic_tab, qta.icon('fa5s.cog', color='#9bcd6c'), "Basic Settings")
        tab_widget.addTab(self.advanced_tab, qta.icon('fa5s.microchip', color='#ff8a5c'), "AI Features")

        bottom_panel = self.create_bottom_panel()
        main_layout.addWidget(bottom_panel)

    def create_basic_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)

        scroll_content = QWidget()
        scroll_content.setStyleSheet("""
            QWidget {
                background-color: transparent;
            }
        """)
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(5, 5, 5, 5)
        scroll_layout.setSpacing(15)

        audio_group = ModernGroupBox("Audio Files")
        audio_layout = QVBoxLayout()
        audio_layout.setContentsMargins(10, 15, 10, 15)
        audio_layout.setSpacing(12)

        player_container = QWidget()
        player_container.setContentsMargins(0, 0, 0, 0)
        player_container.setStyleSheet("""
            QWidget {
                background-color: transparent;
                border: none;
            }
        """)
        player_container_layout = QVBoxLayout(player_container)
        player_container_layout.setContentsMargins(0, 0, 0, 0)

        player_card = QWidget()
        player_card.setContentsMargins(0, 0, 0, 0)
        player_card.setStyleSheet("""
            QWidget {
                background-color: rgba(40, 60, 40, 0.4);
                border-radius: 12px;
                border: none;
            }
        """)
        player_card_layout = QVBoxLayout(player_card)
        player_card_layout.setContentsMargins(15, 12, 15, 12)
        player_card_layout.setSpacing(8)

        player_label_layout = QHBoxLayout()
        player_icon = qta.icon('fa5s.user', color='#6abf4b')
        player_icon_label = QLabel()
        player_icon_label.setPixmap(player_icon.pixmap(16, 16))
        player_label = QLabel("Player Voices")
        player_label.setStyleSheet("font-weight: bold; color: #6abf4b; font-size: 14px; background: transparent;")
        player_label_layout.addWidget(player_icon_label)
        player_label_layout.addWidget(player_label)
        player_label_layout.addStretch()
        player_card_layout.addLayout(player_label_layout)

        self.player_files_label = QLabel("No player voice files loaded")
        self.player_files_label.setStyleSheet(
            "color: #95a5a6; font-style: italic; padding: 5px; background: transparent;"
        )
        player_card_layout.addWidget(self.player_files_label)

        self.load_player_btn = QPushButton(" Load Player Voices (.ogg)")
        self.load_player_btn.setIcon(qta.icon('fa5s.folder-open', color='#e8f5e9'))
        self.load_player_btn.setMinimumHeight(40)
        self.load_player_btn.clicked.connect(self.load_player_voices)
        player_card_layout.addWidget(self.load_player_btn)

        player_container_layout.addWidget(player_card)
        audio_layout.addWidget(player_container)

        opponent_container = QWidget()
        opponent_container.setContentsMargins(0, 0, 0, 0)
        opponent_container.setStyleSheet("""
            QWidget {
                background-color: transparent;
                border: none;
            }
        """)
        opponent_container_layout = QVBoxLayout(opponent_container)
        opponent_container_layout.setContentsMargins(0, 0, 0, 0)

        opponent_card = QWidget()
        opponent_card.setContentsMargins(0, 0, 0, 0)
        opponent_card.setStyleSheet("""
            QWidget {
                background-color: rgba(40, 60, 40, 0.4);
                border-radius: 12px;
                border: none;
            }
        """)
        opponent_card_layout = QVBoxLayout(opponent_card)
        opponent_card_layout.setContentsMargins(15, 12, 15, 12)
        opponent_card_layout.setSpacing(8)

        opponent_label_layout = QHBoxLayout()
        opponent_icon = qta.icon('fa5s.user-secret', color='#ff8a5c')
        opponent_icon_label = QLabel()
        opponent_icon_label.setPixmap(opponent_icon.pixmap(16, 16))
        opponent_label = QLabel("Opponent Voices")
        opponent_label.setStyleSheet("font-weight: bold; color: #ff8a5c; font-size: 14px; background: transparent;")
        opponent_label_layout.addWidget(opponent_icon_label)
        opponent_label_layout.addWidget(opponent_label)
        opponent_label_layout.addStretch()
        opponent_card_layout.addLayout(opponent_label_layout)

        self.opponent_files_label = QLabel("No opponent voice files loaded")
        self.opponent_files_label.setStyleSheet(
            "color: #95a5a6; font-style: italic; padding: 5px; background: transparent;"
        )
        opponent_card_layout.addWidget(self.opponent_files_label)

        self.load_opponent_btn = QPushButton(" Load Opponent Voices (.ogg)")
        self.load_opponent_btn.setIcon(qta.icon('fa5s.folder-open', color='#e8f5e9'))
        self.load_opponent_btn.setMinimumHeight(40)
        self.load_opponent_btn.clicked.connect(self.load_opponent_voices)
        opponent_card_layout.addWidget(self.load_opponent_btn)

        opponent_container_layout.addWidget(opponent_card)
        audio_layout.addWidget(opponent_container)

        audio_group.setLayout(audio_layout)
        scroll_layout.addWidget(audio_group)

        basic_settings_group = ModernGroupBox("Chart Settings")
        basic_layout = QGridLayout()
        basic_layout.setVerticalSpacing(12)
        basic_layout.setHorizontalSpacing(20)

        song_icon = qta.icon('fa5s.music', color='#9bcd6c')
        song_icon_label = QLabel()
        song_icon_label.setPixmap(song_icon.pixmap(16, 16))
        basic_layout.addWidget(song_icon_label, 0, 0)
        basic_layout.addWidget(QLabel("Song Name:"), 0, 1)
        self.song_name_edit = QLineEdit()
        self.song_name_edit.setPlaceholderText("Enter song name")
        self.song_name_edit.setMinimumHeight(35)
        basic_layout.addWidget(self.song_name_edit, 0, 2)

        p2_icon = qta.icon('fa5s.user-secret', color='#ff8a5c')
        p2_icon_label = QLabel()
        p2_icon_label.setPixmap(p2_icon.pixmap(16, 16))
        basic_layout.addWidget(p2_icon_label, 0, 3)
        basic_layout.addWidget(QLabel("Player 2:"), 0, 4)
        self.player2_edit = QLineEdit("dad")
        self.player2_edit.setMinimumHeight(35)
        basic_layout.addWidget(self.player2_edit, 0, 5)

        bpm_icon = qta.icon('fa5s.heartbeat', color='#9bcd6c')
        bpm_icon_label = QLabel()
        bpm_icon_label.setPixmap(bpm_icon.pixmap(16, 16))
        basic_layout.addWidget(bpm_icon_label, 1, 0)
        basic_layout.addWidget(QLabel("BPM:"), 1, 1)
        self.bpm_spinbox = QSpinBox()
        self.bpm_spinbox.setRange(60, 300)
        self.bpm_spinbox.setValue(180)
        self.bpm_spinbox.setMinimumHeight(35)
        basic_layout.addWidget(self.bpm_spinbox, 1, 2)

        gameover_icon = qta.icon('fa5s.skull', color='#ff8a5c')
        gameover_icon_label = QLabel()
        gameover_icon_label.setPixmap(gameover_icon.pixmap(16, 16))
        basic_layout.addWidget(gameover_icon_label, 1, 3)
        basic_layout.addWidget(QLabel("Game Over Char:"), 1, 4)
        self.game_over_edit = QLineEdit("bf-dead")
        self.game_over_edit.setMinimumHeight(35)
        basic_layout.addWidget(self.game_over_edit, 1, 5)

        speed_icon = qta.icon('fa5s.tachometer-alt', color='#9bcd6c')
        speed_icon_label = QLabel()
        speed_icon_label.setPixmap(speed_icon.pixmap(16, 16))
        basic_layout.addWidget(speed_icon_label, 2, 0)
        basic_layout.addWidget(QLabel("Chart Speed:"), 2, 1)
        self.speed_spinbox = QDoubleSpinBox()
        self.speed_spinbox.setRange(0.1, 10.0)
        self.speed_spinbox.setValue(2.6)
        self.speed_spinbox.setSingleStep(0.1)
        self.speed_spinbox.setMinimumHeight(35)
        basic_layout.addWidget(self.speed_spinbox, 2, 2)

        stage_icon = qta.icon('fa5s.theater-masks', color='#9bcd6c')
        stage_icon_label = QLabel()
        stage_icon_label.setPixmap(stage_icon.pixmap(16, 16))
        basic_layout.addWidget(stage_icon_label, 2, 3)
        basic_layout.addWidget(QLabel("Stage:"), 2, 4)
        self.stage_edit = QLineEdit("stage")
        self.stage_edit.setMinimumHeight(35)
        basic_layout.addWidget(self.stage_edit, 2, 5)

        p1_icon = qta.icon('fa5s.user', color='#6abf4b')
        p1_icon_label = QLabel()
        p1_icon_label.setPixmap(p1_icon.pixmap(16, 16))
        basic_layout.addWidget(p1_icon_label, 3, 0)
        basic_layout.addWidget(QLabel("Player 1:"), 3, 1)
        self.player1_edit = QLineEdit("bf")
        self.player1_edit.setMinimumHeight(35)
        basic_layout.addWidget(self.player1_edit, 3, 2)

        gf_icon = qta.icon('fa5s.female', color='#ffab91')
        gf_icon_label = QLabel()
        gf_icon_label.setPixmap(gf_icon.pixmap(16, 16))
        basic_layout.addWidget(gf_icon_label, 3, 3)
        basic_layout.addWidget(QLabel("GF Version:"), 3, 4)
        self.gf_version_edit = QLineEdit("gf")
        self.gf_version_edit.setMinimumHeight(35)
        basic_layout.addWidget(self.gf_version_edit, 3, 5)

        offset_icon = qta.icon('fa5s.clock', color='#9bcd6c')
        offset_icon_label = QLabel()
        offset_icon_label.setPixmap(offset_icon.pixmap(16, 16))
        basic_layout.addWidget(offset_icon_label, 4, 0)
        basic_layout.addWidget(QLabel("Offset (ms):"), 4, 1)
        self.offset_spinbox = QDoubleSpinBox()
        self.offset_spinbox.setRange(-1000, 1000)
        self.offset_spinbox.setValue(0.0)
        self.offset_spinbox.setSingleStep(0.1)
        self.offset_spinbox.setMinimumHeight(35)
        basic_layout.addWidget(self.offset_spinbox, 4, 2)

        sustain_icon = qta.icon('fa5s.arrows-alt-h', color='#9bcd6c')
        sustain_icon_label = QLabel()
        sustain_icon_label.setPixmap(sustain_icon.pixmap(16, 16))
        basic_layout.addWidget(sustain_icon_label, 5, 0)
        basic_layout.addWidget(QLabel("Default Sustain (ms):"), 5, 1)
        self.sustain_spinbox = QSpinBox()
        self.sustain_spinbox.setRange(0, 5000)
        self.sustain_spinbox.setValue(0)
        self.sustain_spinbox.setMinimumHeight(35)
        basic_layout.addWidget(self.sustain_spinbox, 5, 2)

        basic_settings_group.setLayout(basic_layout)
        scroll_layout.addWidget(basic_settings_group)

        detection_group = ModernGroupBox("Note Detection")
        detection_layout = QVBoxLayout()
        detection_layout.setSpacing(10)

        self.prevent_consecutive_check = QCheckBox("Prevent consecutive notes")
        self.prevent_consecutive_check.setStyleSheet("padding: 5px;")
        detection_layout.addWidget(self.prevent_consecutive_check)

        consecutive_layout = QHBoxLayout()
        consecutive_layout.addWidget(QLabel("Max consecutive:"))
        self.max_consecutive_spinbox = QSpinBox()
        self.max_consecutive_spinbox.setRange(0, 3)
        self.max_consecutive_spinbox.setValue(1)
        self.max_consecutive_spinbox.setMinimumHeight(35)
        consecutive_layout.addWidget(self.max_consecutive_spinbox)
        consecutive_layout.addStretch()
        detection_layout.addLayout(consecutive_layout)

        self.prevent_consecutive_check.toggled.connect(
            lambda checked: self.max_consecutive_spinbox.setEnabled(checked)
        )
        self.max_consecutive_spinbox.setEnabled(self.prevent_consecutive_check.isChecked())

        detection_group.setLayout(detection_layout)
        scroll_layout.addWidget(detection_group)

        scroll_layout.addStretch()

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        return tab

    def create_advanced_tab(self):
        tab = QWidget()
        tab.setStyleSheet("""
            QWidget {
                background-color: transparent;
            }
        """)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(15)
        scroll_layout.setContentsMargins(0, 0, 0, 0)

        sustain_group = ModernGroupBox("AI Sustain Detection")
        sustain_layout = QGridLayout()
        sustain_layout.setContentsMargins(15, 20, 15, 15)
        sustain_layout.setVerticalSpacing(12)
        sustain_layout.setHorizontalSpacing(20)

        self.enable_sustain_check = QCheckBox("Enable AI Sustain Detection")
        self.enable_sustain_check.setStyleSheet("""
            QCheckBox {
                padding: 5px;
                font-weight: bold;
                color: #6abf4b;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid rgba(69, 99, 69, 0.6);
                background-color: rgba(15, 25, 15, 0.8);
            }
            QCheckBox::indicator:checked {
                background-color: #4caf50;
                border: 1px solid #4caf50;
            }
        """)
        sustain_layout.addWidget(self.enable_sustain_check, 0, 0, 1, 2)

        self.min_sustain_spin = QSpinBox()
        self.min_sustain_spin.setRange(50, 1000)
        self.min_sustain_spin.setValue(150)
        self.min_sustain_spin.setMinimumHeight(35)
        sustain_layout.addWidget(QLabel("Min Sustain (ms):"), 1, 0)
        sustain_layout.addWidget(self.min_sustain_spin, 1, 1)

        self.sustain_threshold_spin = QDoubleSpinBox()
        self.sustain_threshold_spin.setRange(-60.0, 0.0)
        self.sustain_threshold_spin.setValue(-30.0)
        self.sustain_threshold_spin.setSingleStep(0.5)
        self.sustain_threshold_spin.setMinimumHeight(35)
        sustain_layout.addWidget(QLabel("Sustain Threshold (dB):"), 2, 0)
        sustain_layout.addWidget(self.sustain_threshold_spin, 2, 1)

        self.sustain_release_spin = QDoubleSpinBox()
        self.sustain_release_spin.setRange(-60.0, 0.0)
        self.sustain_release_spin.setValue(-28.0)
        self.sustain_release_spin.setSingleStep(0.5)
        self.sustain_release_spin.setMinimumHeight(35)
        sustain_layout.addWidget(QLabel("Release Threshold (dB):"), 3, 0)
        sustain_layout.addWidget(self.sustain_release_spin, 3, 1)

        self.sustain_extension_spin = QSpinBox()
        self.sustain_extension_spin.setRange(0, 500)
        self.sustain_extension_spin.setValue(150)
        self.sustain_extension_spin.setMinimumHeight(35)
        sustain_layout.addWidget(QLabel("Extension (ms):"), 4, 0)
        sustain_layout.addWidget(self.sustain_extension_spin, 4, 1)

        self.min_silence_spin = QSpinBox()
        self.min_silence_spin.setRange(0, 200)
        self.min_silence_spin.setValue(50)
        self.min_silence_spin.setMinimumHeight(35)
        sustain_layout.addWidget(QLabel("Min Silence (ms):"), 5, 0)
        sustain_layout.addWidget(self.min_silence_spin, 5, 1)

        self.sustain_widgets = [
            self.min_sustain_spin, self.sustain_threshold_spin,
            self.sustain_release_spin, self.sustain_extension_spin, self.min_silence_spin
        ]

        self.enable_sustain_check.toggled.connect(self.toggle_sustain_widgets)
        
        sustain_group.setLayout(sustain_layout)
        scroll_layout.addWidget(sustain_group)

        tonality_group = ModernGroupBox("AI Tonality Detection")
        tonality_layout = QGridLayout()
        tonality_layout.setContentsMargins(15, 20, 15, 15)
        tonality_layout.setVerticalSpacing(12)
        tonality_layout.setHorizontalSpacing(20)

        self.enable_tonality_check = QCheckBox("Enable AI Tonality Detection")
        self.enable_tonality_check.setStyleSheet("""
            QCheckBox {
                padding: 5px;
                font-weight: bold;
                color: #ff8a5c;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid rgba(69, 99, 69, 0.6);
                background-color: rgba(15, 25, 15, 0.8);
            }
            QCheckBox::indicator:checked {
                background-color: #ff8a5c;
                border: 1px solid #ff8a5c;
            }
        """)
        tonality_layout.addWidget(self.enable_tonality_check, 0, 0, 1, 2)

        self.low_threshold_spin = QDoubleSpinBox()
        self.low_threshold_spin.setRange(50.0, 300.0)
        self.low_threshold_spin.setValue(150.0)
        self.low_threshold_spin.setSingleStep(10.0)
        self.low_threshold_spin.setMinimumHeight(35)
        tonality_layout.addWidget(QLabel("Low C Threshold (Hz):"), 1, 0)
        tonality_layout.addWidget(self.low_threshold_spin, 1, 1)

        self.mid_threshold_spin = QDoubleSpinBox()
        self.mid_threshold_spin.setRange(200.0, 400.0)
        self.mid_threshold_spin.setValue(250.0)
        self.mid_threshold_spin.setSingleStep(10.0)
        self.mid_threshold_spin.setMinimumHeight(35)
        tonality_layout.addWidget(QLabel("Mid C Threshold (Hz):"), 2, 0)
        tonality_layout.addWidget(self.mid_threshold_spin, 2, 1)

        self.high_threshold_spin = QDoubleSpinBox()
        self.high_threshold_spin.setRange(300.0, 600.0)
        self.high_threshold_spin.setValue(400.0)
        self.high_threshold_spin.setSingleStep(10.0)
        self.high_threshold_spin.setMinimumHeight(35)
        tonality_layout.addWidget(QLabel("High C Threshold (Hz):"), 3, 0)
        tonality_layout.addWidget(self.high_threshold_spin, 3, 1)

        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setValue(0.8)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setMinimumHeight(35)
        tonality_layout.addWidget(QLabel("Confidence Threshold:"), 4, 0)
        tonality_layout.addWidget(self.confidence_spin, 4, 1)

        self.tonality_widgets = [
            self.low_threshold_spin, self.mid_threshold_spin,
            self.high_threshold_spin, self.confidence_spin
        ]

        self.enable_tonality_check.toggled.connect(self.toggle_tonality_widgets)

        tonality_group.setLayout(tonality_layout)
        scroll_layout.addWidget(tonality_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        return tab

    def toggle_sustain_widgets(self, enabled):
        for widget in self.sustain_widgets:
            widget.setEnabled(enabled)

    def toggle_tonality_widgets(self, enabled):
        for widget in self.tonality_widgets:
            widget.setEnabled(enabled)

    def create_bottom_panel(self):
        panel = QWidget()
        panel.setStyleSheet("""
            background-color: rgba(25, 45, 25, 0.5);
            border-radius: 15px;
            padding: 10px;
        """)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        progress_widget = QWidget()
        progress_layout = QHBoxLayout(progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)

        self.progress_label = QLabel("Ready to generate chart")
        self.progress_label.setStyleSheet(
            "color: #9bcd6c; font-weight: bold; min-width: 250px; font-size: 12px;"
        )
        self.progress_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress_bar)

        layout.addWidget(progress_widget)

        button_widget = QWidget()
        button_layout = QHBoxLayout(button_widget)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(15)

        self.generate_btn = QPushButton(" Generate Chart")
        self.generate_btn.setIcon(qta.icon('fa5s.play', color='#ffffff'))
        self.generate_btn.clicked.connect(self.start_generation)
        self.generate_btn.setMinimumHeight(55)
        self.generate_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2e7d32, stop:1 #1b5e20);
                font-size: 15px;
                font-weight: bold;
                color: white;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #388e3c, stop:1 #2e7d32);
            }
            QPushButton:pressed {
                background: #1b5e20;
            }
        """)

        self.save_btn = QPushButton(" Save Chart")
        self.save_btn.setIcon(qta.icon('fa5s.save', color='#ffffff'))
        self.save_btn.clicked.connect(self.save_chart)
        self.save_btn.setMinimumHeight(55)
        self.save_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.save_btn.setEnabled(False)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #00695c, stop:1 #004d40);
                font-size: 15px;
                font-weight: bold;
                color: white;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #00897b, stop:1 #00695c);
            }
            QPushButton:pressed {
                background: #004d40;
            }
            QPushButton:disabled {
                background: #2e5c2e;
                color: #7f8c8d;
            }
        """)
        
        self.cancel_btn = QPushButton(" Cancel")
        self.cancel_btn.setIcon(qta.icon('fa5s.times', color='#ffffff'))
        self.cancel_btn.clicked.connect(self.cancel_generation)
        self.cancel_btn.setMinimumHeight(55)
        self.cancel_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #c62828, stop:1 #b71c1c);
                font-size: 15px;
                font-weight: bold;
                color: white;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e53935, stop:1 #c62828);
            }
            QPushButton:pressed {
                background: #b71c1c;
            }
        """)

        button_layout.addWidget(self.generate_btn)
        button_layout.addWidget(self.save_btn)
        button_layout.addWidget(self.cancel_btn)

        layout.addWidget(button_widget)

        status_widget = QWidget()
        status_widget.setStyleSheet("""
            background-color: rgba(0, 0, 0, 0.3);
            border-radius: 10px;
        """)
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(10, 5, 10, 5)
        status_layout.addStretch()

        status_icon = qta.icon('fa5s.info-circle', color='#9bcd6c')
        status_icon_label = QLabel()
        status_icon_label.setPixmap(status_icon.pixmap(14, 14))

        self.status_label = QLabel(
            "Load audio files and click 'Generate Chart' to begin"
        )
        self.status_label.setStyleSheet(
            "color: #9bcd6c; font-style: italic; font-size: 11px;"
        )
        self.status_label.setAlignment(Qt.AlignCenter)

        status_layout.addWidget(status_icon_label)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        layout.addWidget(status_widget)

        return panel

    def load_settings(self):
        self.song_name_edit.setText(self.chart_generator.song_name)
        self.bpm_spinbox.setValue(self.chart_generator.bpm)
        self.speed_spinbox.setValue(self.chart_generator.speed)
        self.player1_edit.setText(self.chart_generator.player1_char)
        self.player2_edit.setText(self.chart_generator.player2_char)
        self.game_over_edit.setText(self.chart_generator.game_over_char)
        self.stage_edit.setText(self.chart_generator.stage)
        self.gf_version_edit.setText(self.chart_generator.gf_version)
        self.offset_spinbox.setValue(self.chart_generator.offset)
        self.sustain_spinbox.setValue(self.chart_generator.default_sustain_length)

        self.enable_sustain_check.setChecked(self.chart_generator.enable_ai_sustain)
        self.min_sustain_spin.setValue(self.chart_generator.min_sustain_duration_ms)
        self.sustain_threshold_spin.setValue(self.chart_generator.sustain_threshold_db)
        self.sustain_release_spin.setValue(
            self.chart_generator.sustain_release_threshold_db
        )
        self.sustain_extension_spin.setValue(self.chart_generator.sustain_extension_ms)
        self.min_silence_spin.setValue(
            self.chart_generator.min_silence_between_sustains_ms
        )

        self.toggle_sustain_widgets(self.enable_sustain_check.isChecked())

        self.enable_tonality_check.setChecked(self.chart_generator.enable_ai_tonality)
        self.low_threshold_spin.setValue(self.chart_generator.pitch_low_c_threshold)
        self.mid_threshold_spin.setValue(self.chart_generator.pitch_mid_c_threshold)
        self.high_threshold_spin.setValue(self.chart_generator.pitch_high_c_threshold)
        self.confidence_spin.setValue(self.chart_generator.pitch_confidence_threshold)

        self.toggle_tonality_widgets(self.enable_tonality_check.isChecked())

        self.prevent_consecutive_check.setChecked(
            self.chart_generator.prevent_consecutive_notes
        )
        self.max_consecutive_spinbox.setValue(
            self.chart_generator.max_consecutive_allowed_count
        )

    def update_progress(self, value, message=""):
        self.progress_bar.setValue(int(value))
        self.progress_label.setText(f"{message[:100]}")

        if value < 100 and value > 0:
            self.status_label.setText(f"Generating: {message[:80]}...")

    def load_player_voices(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Player Voice Files", "", "OGG Files (*.ogg);;All Files (*)"
        )

        if files:
            self.load_player_btn.setEnabled(False)
            self.load_player_btn.setText("Loading...")
            self.status_label.setText("Loading player voices, please wait...")

            self.audio_load_thread = AudioLoadThread(files, is_player=True)
            self.audio_load_thread.loading_complete.connect(self.on_player_voices_loaded)
            self.audio_load_thread.loading_error.connect(self.on_audio_load_error)
            self.audio_load_thread.start()

    def load_opponent_voices(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Opponent Voice Files", "", "OGG Files (*.ogg);;All Files (*)"
        )

        if files:
            self.load_opponent_btn.setEnabled(False)
            self.load_opponent_btn.setText("Loading...")
            self.status_label.setText("Loading opponent voices, please wait...")

            self.audio_load_thread = AudioLoadThread(files, is_player=False)
            self.audio_load_thread.loading_complete.connect(self.on_opponent_voices_loaded)
            self.audio_load_thread.loading_error.connect(self.on_audio_load_error)
            self.audio_load_thread.start()

    def on_player_voices_loaded(self, result, is_player):
        self.load_player_btn.setEnabled(True)
        self.load_player_btn.setText(" Load Player Voices (.ogg)")
        self.load_player_btn.setIcon(qta.icon('fa5s.folder-open', color='#e8f5e9'))
        
        if result is None:
            QMessageBox.warning(
                self,
                "Load Error",
                "None of the selected files could be loaded.\nMake sure they are valid OGG audio files.",
            )
            self.player_files_label.setText("No player voice files loaded")
            self.status_label.setText("Failed to load player voices")
        else:
            audio_data, loaded_files = result
            actual_count = self.chart_generator.load_audio_files(audio_data, loaded_files, is_player=True)
            
            self.player_files_label.setText(f"{actual_count} player voice file(s) loaded")
            self.player_files_label.setStyleSheet("color: #81c784; font-weight: bold;")
            self.status_label.setText(f"Loaded {actual_count} player voice file(s)")

    def on_opponent_voices_loaded(self, result, is_player):
        self.load_opponent_btn.setEnabled(True)
        self.load_opponent_btn.setText(" Load Opponent Voices (.ogg)")
        self.load_opponent_btn.setIcon(qta.icon('fa5s.folder-open', color='#e8f5e9'))
        
        if result is None:
            QMessageBox.warning(
                self,
                "Load Error",
                "None of the selected files could be loaded.\nMake sure they are valid OGG audio files.",
            )
            self.opponent_files_label.setText("No opponent voice files loaded")
            self.status_label.setText("Failed to load opponent voices")
        else:
            audio_data, loaded_files = result
            actual_count = self.chart_generator.load_audio_files(audio_data, loaded_files, is_player=False)
            
            self.opponent_files_label.setText(f"{actual_count} opponent voice file(s) loaded")
            self.opponent_files_label.setStyleSheet("color: #ffab91; font-weight: bold;")
            self.status_label.setText(f"Loaded {actual_count} opponent voice file(s)")

    def on_audio_load_error(self, error_message, is_player):
        if is_player:
            self.load_player_btn.setEnabled(True)
            self.load_player_btn.setText(" Load Player Voices (.ogg)")
            self.load_player_btn.setIcon(qta.icon('fa5s.folder-open', color='#e8f5e9'))
            self.player_files_label.setText("Error loading player voices")
            self.status_label.setText("Error loading player voices")
        else:
            self.load_opponent_btn.setEnabled(True)
            self.load_opponent_btn.setText(" Load Opponent Voices (.ogg)")
            self.load_opponent_btn.setIcon(qta.icon('fa5s.folder-open', color='#e8f5e9'))
            self.opponent_files_label.setText("Error loading opponent voices")
            self.status_label.setText("Error loading opponent voices")
        
        QMessageBox.critical(self, "Loading Error", f"Failed to load audio files:\n{error_message}")

    def update_generator_settings(self):
        self.chart_generator.bpm = self.bpm_spinbox.value()
        self.chart_generator.song_name = self.song_name_edit.text() or "New Song"
        self.chart_generator.player1_char = self.player1_edit.text() or "bf"
        self.chart_generator.player2_char = self.player2_edit.text() or "dad"
        self.chart_generator.game_over_char = self.game_over_edit.text() or "bf-dead"
        self.chart_generator.speed = self.speed_spinbox.value()
        self.chart_generator.default_sustain_length = self.sustain_spinbox.value()
        self.chart_generator.offset = self.offset_spinbox.value()
        self.chart_generator.gf_version = self.gf_version_edit.text() or "gf"
        self.chart_generator.stage = self.stage_edit.text() or "stage"

        self.chart_generator.prevent_consecutive_notes = (
            self.prevent_consecutive_check.isChecked()
        )
        self.chart_generator.max_consecutive_allowed_count = self.max_consecutive_spinbox.value()

        self.chart_generator.enable_ai_sustain = self.enable_sustain_check.isChecked()
        if self.enable_sustain_check.isChecked():
            self.chart_generator.min_sustain_duration_ms = self.min_sustain_spin.value()
            self.chart_generator.sustain_threshold_db = (
                self.sustain_threshold_spin.value()
            )
            self.chart_generator.sustain_release_threshold_db = (
                self.sustain_release_spin.value()
            )
            self.chart_generator.sustain_extension_ms = (
                self.sustain_extension_spin.value()
            )
            self.chart_generator.min_silence_between_sustains_ms = (
                self.min_silence_spin.value()
            )

        self.chart_generator.enable_ai_tonality = self.enable_tonality_check.isChecked()
        if self.enable_tonality_check.isChecked():
            self.chart_generator.pitch_low_c_threshold = self.low_threshold_spin.value()
            self.chart_generator.pitch_mid_c_threshold = self.mid_threshold_spin.value()
            self.chart_generator.pitch_high_c_threshold = (
                self.high_threshold_spin.value()
            )
            self.chart_generator.pitch_confidence_threshold = (
                self.confidence_spin.value()
            )

    def start_generation(self):
        if not self.chart_generator.song_name:
            QMessageBox.warning(self, "Warning", "Please enter a song name.")
            return

        if (
            self.chart_generator.player_voices is None
            and self.chart_generator.opponent_voices is None
        ):
            QMessageBox.warning(self, "Warning", "Please load at least one voice file.")
            return

        self.update_generator_settings()

        self.set_ui_enabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting generation...")
        self.status_label.setText("Generating chart, please wait...")
        
        self.generation_thread = ChartGenerationThread(self.chart_generator)
        self.generation_thread.progress_updated.connect(self.update_progress)
        self.generation_thread.generation_complete.connect(self.on_generation_complete)
        self.generation_thread.error_occurred.connect(self.on_generation_error)
        self.generation_thread.start()

    def cancel_generation(self):
        if self.generation_thread and self.generation_thread.isRunning():
            self.generation_thread.cancel()
            self.status_label.setText("Cancelling generation...")
            self.cancel_btn.setEnabled(False)

    def on_generation_complete(self, chart_data):
        if chart_data is None:
            self.on_generation_error("Generation was cancelled")
            return
            
        self.current_chart_data = chart_data
        self.set_ui_enabled(True)
        self.save_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)

        self.progress_label.setText("Chart generation complete!")
        self.status_label.setText("Chart generated successfully! You can now save it.")
        QMessageBox.information(self, "Success", "Chart generated successfully!")

    def on_generation_error(self, error_message):
        self.set_ui_enabled(True)
        self.cancel_btn.setEnabled(False)
        self.progress_label.setText("Error occurred")
        self.status_label.setText(f"Error: {error_message[:100]}")
        
        if "cancelled" not in error_message.lower():
            QMessageBox.critical(self, "Error", f"An error occurred:\n{error_message}")

    def set_ui_enabled(self, enabled):
        buttons = [
            self.load_player_btn,
            self.load_opponent_btn,
            self.generate_btn,
        ]

        inputs = [
            self.bpm_spinbox,
            self.speed_spinbox,
            self.sustain_spinbox,
            self.offset_spinbox,
            self.min_sustain_spin,
            self.sustain_threshold_spin,
            self.sustain_release_spin,
            self.sustain_extension_spin,
            self.min_silence_spin,
            self.low_threshold_spin,
            self.mid_threshold_spin,
            self.high_threshold_spin,
            self.confidence_spin,
            self.max_consecutive_spinbox,
        ]

        edits = [
            self.song_name_edit,
            self.player1_edit,
            self.player2_edit,
            self.game_over_edit,
            self.stage_edit,
            self.gf_version_edit,
        ]

        checkboxes = [
            self.prevent_consecutive_check,
            self.enable_sustain_check,
            self.enable_tonality_check,
        ]

        for widget in buttons + inputs + edits:
            if widget:
                widget.setEnabled(enabled)

        for checkbox in checkboxes:
            if checkbox:
                checkbox.setEnabled(enabled)

        if not enabled:
            self.save_btn.setEnabled(False)

    def save_chart(self):
        if self.current_chart_data is None:
            QMessageBox.warning(
                self, "Warning", "No chart to save. Generate a chart first."
            )
            return

        song_name = self.chart_generator.song_name.replace(" ", "_")
        default_filename = f"{song_name}.json"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Chart", default_filename, "JSON Files (*.json);;All Files (*)"
        )

        if file_path:
            try:
                if not file_path.lower().endswith(".json"):
                    file_path += ".json"

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(self.current_chart_data, f, indent=4)

                QMessageBox.information(
                    self, "Success", f"Chart saved to:\n{file_path}"
                )

            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save chart:\n{e}")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("FNF Chart Generator Redux")
    app.setStyle("Fusion")

    splash_path = resource_path("assets/fnfcgr-splash.png")
    screen_splash = QPixmap(splash_path)

    intro_screen = QSplashScreen(screen_splash)
    intro_screen.show()

    app.processEvents()

    for i in range(1, 6):
        intro_screen.showMessage(
            f"{i*20}% loading", 
            Qt.AlignBottom | Qt.AlignCenter, 
            Qt.white
        )
        app.processEvents()
        time.sleep(0.6)

    icon_path = resource_path("assets/icon.ico")

    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = ChartGeneratorApp()
    window.show()

    intro_screen.finish(window)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()