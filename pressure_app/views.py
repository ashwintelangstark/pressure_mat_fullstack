from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q
from .models import Doctor, Patient, PressureReading, PatientVideo, GameSession
from django.core.cache import cache
import time
import subprocess
import sys
import os
from pathlib import Path
from django.views.decorators.csrf import csrf_exempt
import json
from datetime import datetime
from django.conf import settings
from django.core.files import File
from django.core.files.base import ContentFile
import uuid

def landing_page(request):
    return render(request, 'pressure_app/landing.html')

def login_page(request):
    if request.user.is_authenticated:
        return redirect('home')
    return render(request, 'pressure_app/login.html')

def loading_page(request):
    patient_id = request.GET.get('patient_id')
    return render(request, 'pressure_app/loading.html', {'patient_id': patient_id})

def doctor_register(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            username = data.get('username')
            email = data.get('email')
            password = data.get('password')
            first_name = data.get('first_name')
            last_name = data.get('last_name')
            license_number = data.get('license_number')
            hospital = data.get('hospital')
            specialization = data.get('specialization')
            
            if Doctor.objects.filter(username=username).exists():
                return JsonResponse({'error': 'Username already exists'}, status=400)
                
            if Doctor.objects.filter(license_number=license_number).exists():
                return JsonResponse({'error': 'License number already registered'}, status=400)
                
            doctor = Doctor.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                license_number=license_number,
                hospital=hospital,
                specialization=specialization
            )
            
            login(request, doctor)
            return JsonResponse({'success': True, 'redirect': '/home/'})
            
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid method'}, status=405)


def doctor_login(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            username = data.get('username')
            password = data.get('password')
            
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                return JsonResponse({'success': True, 'redirect': '/home/'})
            else:
                return JsonResponse({'error': 'Invalid credentials'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid method'}, status=405)

def doctor_logout(request):
    logout(request)
    return redirect ('landing_page')

@login_required
def home(request):
    patients = Patient.objects.all().order_by('-id')

    paginator = Paginator(patients, 9)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, 'pressure_app/home.html', {
        'patients': page_obj,
        'page_obj': page_obj,
    })

def verify_patient(request, patient_id):
    """Verify or create a patient record"""
    try:
        patient, created = Patient.objects.get_or_create(
            patient_id=patient_id,
            defaults={'name': f"Patient {patient_id}"}
        )
        return JsonResponse({
            'exists': not created,
            'created': created,
            'patient_id': patient_id
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)

def start_visualization(request):
    """Redirect to integrated game & pressure visualization session"""
    patient_id = request.GET.get('patient_id')
    if not patient_id:
        first_patient = Patient.objects.first()
        patient_id = first_patient.patient_id if first_patient else '1'
    return redirect('game_page', patient_id=patient_id)


def get_latest_readings(request):
    readings = PressureReading.objects.order_by('-timestamp')[:10]
    data = {
        'readings': [
            {
                'timestamp': reading.timestamp.isoformat(),
                'displacement_x': reading.displacement_x,
                'displacement_y': reading.displacement_y,
                'movement_status': reading.movement_status
            }
            for reading in readings
        ]
    }
    return JsonResponse(data)

@csrf_exempt
def save_reading(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            # Save to database or process data here
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': str(e)}, status=400)
    return JsonResponse({'status': 'invalid method'}, status=405)

@csrf_exempt
def save_session(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            patient, created = Patient.objects.get_or_create(
                patient_id=data['patient_id'],
                defaults={'name': f"Patient {data['patient_id']}"}
            )
            
            for reading in data['readings']:
                PressureReading.objects.create(
                    patient=patient,
                    timestamp=reading['timestamp'],
                    pressure_data=reading['pressure_data'],
                    displacement_x=reading['displacement_x'],
                    displacement_y=reading['displacement_y'],
                    movement_status=reading['movement_status'],
                    is_analysis_complete=True
                )
                
            return JsonResponse({'success': True, 'patient_id': patient.patient_id})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)



def get_patient_readings(request, patient_id):
    try:
        patient = Patient.objects.get(patient_id=patient_id)
        readings = patient.readings.filter(is_analysis_complete=True).order_by('-timestamp')

        videos = PatientVideo.objects.filter(patient=patient).order_by('-timestamp')

        for video in videos:
            video.observation = "Stable posture detected"

            latest_reading = patient.readings.order_by('-timestamp').first()

            if latest_reading:
                if abs(latest_reading.displacement_x) > 50:
                    video.observation = "Right/Left imbalance observed"

                elif abs(latest_reading.displacement_y) > 50:
                    video.observation = "Forward/Backward instability observed"

                elif "RIGHT" in latest_reading.movement_status:
                    video.observation = "Patient shifted weight towards right side"

                elif "LEFT" in latest_reading.movement_status:
                    video.observation = "Patient shifted weight towards left side"

                else:
                    video.observation = "Balanced pressure distribution detected"

        videos = patient.videos.all().order_by('-timestamp')

        video_count = videos.count()
        
        data = {
            'patient': {
                'name': patient.name,
                'patient_id': patient.patient_id
            },
            'readings': [
                {
                    'id': reading.id,
                    'timestamp': reading.timestamp.isoformat(),
                    'displacement_x': reading.displacement_x,
                    'displacement_y': reading.displacement_y,
                    'movement_status': reading.movement_status,
                    'type': 'reading'
                }
                for reading in readings
            ],
            'videos': [
                {
                    'id': video.id,
                    'timestamp': video.timestamp.isoformat(),
                    'video_url': request.build_absolute_uri(video.video_file.url),
                    'type': 'video'
                }
                for video in videos
            ]
        }

        # observation = """
        # Weekly Analysis Summary:
        # Patient demonstrates overall stable pressure distribution during monitored sessions.
        # Minor variations in weight shifting were observed and should continue to be monitored.
        # No severe balance abnormalities detected during recent recordings.
        # Regular follow-up sessions are recommended to track progression and maintain foot health.
        # """

        # latest_reading = readings.first()

        # if latest_reading:
        #     if abs(latest_reading.displacement_x) < 20 and abs(latest_reading.displacement_y) < 20:
        #         observation = "Good balance and stable posture detected."
        #     elif abs(latest_reading.displacement_x) > 50:
        #         observation = "Significant lateral weight shift detected."
        #     elif abs(latest_reading.displacement_y) > 50:
        #         observation = "Forward/backward instability observed."
        #     else:
        #         observation = latest_reading.movement_status

        from datetime import timedelta
        from django.utils import timezone

        one_week_ago = timezone.now() - timedelta(days=7)

        weekly_readings = readings.filter(
            timestamp__gte=one_week_ago
        )

        total_sessions = weekly_readings.count()

        stable_sessions = 0
        imbalance_sessions = 0

        left_bias = 0
        right_bias = 0

        cop_stable = 0
        cop_unstable = 0

        for reading in weekly_readings:

            pressure = reading.pressure_data

            if not pressure:
                continue

            try:
                left_pressure = 0
                right_pressure = 0

                for row in pressure:
                    left_pressure += sum(row[:10])
                    right_pressure += sum(row[10:])

                total = left_pressure + right_pressure

                if total > 0:

                    left_pct = (left_pressure / total) * 100
                    right_pct = (right_pressure / total) * 100

                    if abs(left_pct - right_pct) <= 10:
                        stable_sessions += 1
                    else:
                        imbalance_sessions += 1

                    if left_pct > right_pct:
                        left_bias += 1
                    else:
                        right_bias += 1

                if (
                    abs(reading.displacement_x) < 20 and
                    abs(reading.displacement_y) < 20
                ):
                    cop_stable += 1
                else:
                    cop_unstable += 1

            except Exception:
                pass


        if videos.count() < 3:

            observation = """
            <ul class='observation-list'>
                <li>Insufficient heatmap recordings for weekly assessment.</li>
                <li>Minimum 3 sessions required for analysis.</li>
            </ul>
            """

        else:

            dominant_side = "Left" if left_bias > right_bias else "Right"

            peak_pressure = 0
            active_sensors = 0

            for reading in weekly_readings:

                pressure = reading.pressure_data

                if not pressure:
                    continue

                for row in pressure:
                    for value in row:

                        if value > 0:
                            active_sensors += 1

                        if value > peak_pressure:
                            peak_pressure = value


            dominant_side = "Left" if left_bias > right_bias else "Right"


            if peak_pressure > 180:

                pressure_level = "High plantar pressure"

            elif peak_pressure > 100:

                pressure_level = "Moderate plantar pressure"

            else:

                pressure_level = "Low plantar pressure"


            if active_sensors < 20:

                contact_area = "Reduced foot contact area"

            elif active_sensors < 50:

                contact_area = "Partial foot contact area"

            else:

                contact_area = "Full foot contact area"


            observation = f"""
            <ul class='observation-list'>

            <li>{pressure_level} detected.</li>

            <li>{contact_area} observed.</li>

            <li>Dominant loading towards {dominant_side} foot.</li>

            <li>Peak pressure recorded: {round(peak_pressure,1)}</li>

            </ul>
            """

            # if imbalance_sessions <= total_sessions * 0.2:

            #     observation = f"""
            #     <ul class='observation-list'>
            #         <li>Pressure distribution remains symmetrical.</li>

            #         <li>COP stability is within normal range.</li>

            #         <li>Dominant loading pattern:
            #         {dominant_side} foot.</li>

            #         <li>Continue routine monitoring.</li>
            #     </ul>
            #     """

            # elif imbalance_sessions <= total_sessions * 0.5:

            #     observation = f"""
            #     <ul class='observation-list'>
            #         <li>Mild pressure asymmetry detected.</li>

            #         <li>Increased loading observed on
            #         {dominant_side} foot.</li>

            #         <li>Minor balance deviations noted.</li>

            #         <li>Follow-up monitoring recommended.</li>
            #     </ul>
            #     """

            # else:

            #     observation = f"""
            #     <ul class='observation-list'>
            #         <li>Significant pressure imbalance detected.</li>

            #         <li>Persistent weight-bearing asymmetry
            #         towards {dominant_side} foot.</li>

            #         <li>Increased COP variability observed.</li>

            #         <li>Clinical review recommended.</li>
            #     </ul>
            #     """

        weekly_stable = stable_sessions
        weekly_imbalanced = imbalance_sessions

        game_sessions = patient.game_sessions.order_by('-started_at')

        return render(request, 'pressure_app/patient_history.html', {
            'patient': patient,
            'readings': readings,
            'videos': videos,
            'observation': observation,
            'stable_sessions': weekly_stable,
            'imbalanced_sessions': weekly_imbalanced,
            'game_sessions': game_sessions,
        })
    
    except Patient.DoesNotExist:
        return JsonResponse({'error': 'Patient not found'}, status=404)

@csrf_exempt
def save_video(request):
    if request.method == 'POST':
        try:
            print("POST DATA =", request.POST)
            print("FILES =", request.FILES)
            print("PATIENT ID =", request.POST.get('patient_id'))
            print("VIDEO =", request.FILES.get('video'))

            patient_id = request.POST.get('patient_id')
            video_file = request.FILES.get('video')
            notes = request.POST.get('notes', '')

            if not patient_id or not video_file:
                return JsonResponse({'success': False, 'error': 'Missing patient_id or video file'}, status=400)

            # Create patient if doesn't exist
            patient, _ = Patient.objects.get_or_create(
                patient_id=patient_id,
                defaults={'name': f"Patient {patient_id}"}
            )

            # Save original file temporarily
            temp_filename = f"{uuid.uuid4()}_input.mp4"
            temp_input_path = os.path.join(settings.MEDIA_ROOT, 'temp', temp_filename)

            os.makedirs(os.path.dirname(temp_input_path), exist_ok=True)
            with open(temp_input_path, 'wb+') as destination:
                for chunk in video_file.chunks():
                    destination.write(chunk)

            # Define output (converted) path
            converted_filename = f"{uuid.uuid4()}_converted.mp4"
            converted_path = os.path.join(settings.MEDIA_ROOT, 'videos', converted_filename)
            os.makedirs(os.path.dirname(converted_path), exist_ok=True)

            ffmpeg_command = [
                'ffmpeg',
                '-i', temp_input_path,
                '-vf', 'setpts=4.0*PTS',
                '-filter:a', 'atempo=0.5,atempo=0.5',
                '-vcodec', 'libx264',
                '-acodec', 'aac',
                '-strict', 'experimental',
                '-y',
                converted_path
            ]

            # subprocess.run(ffmpeg_command, check=True)
            result = subprocess.run(
                ffmpeg_command,
                capture_output=True,
                text=True
            )

            print(result.stdout)
            print(result.stderr)

            # Open converted video file and save to model
            with open(converted_path, 'rb') as f:
                django_file = File(f)
                video = PatientVideo.objects.create(
                    patient=patient,
                    notes=notes
                )
                video.video_file.save(converted_filename, django_file, save=True)

            # Cleanup temporary files
            os.remove(temp_input_path)
            os.remove(converted_path)

            return JsonResponse({
                'success': True,
                'video_url': video.video_file.url,
                'message': 'Video uploaded and converted successfully'
            })

        except subprocess.CalledProcessError as e:
            return JsonResponse({'success': False, 'error': f'FFmpeg error: {str(e)}'}, status=500)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)


def add_patient(request):
    if request.method == 'POST':
        name = request.POST.get('name')
        patient_id = request.POST.get('patient_id')  # adjust if your form uses another name

        if name and patient_id:
            Patient.objects.create(
                name=name,
                patient_id=patient_id
            )
            return redirect('home')  # Redirect to patient listing page

    return render(request, 'pressure_app/add_patient.html')

def delete_patient(request, patient_id):
    patient = get_object_or_404(Patient, patient_id=patient_id)
    patient.delete()
    return redirect('home')


# ---------------------------------------------------------------------------
# Game mode ("Collect the Stars")
#
# Data flow:
#   mat sensor --(serial)--> game_bridge.py --(HTTP POST every ~100ms)-->
#   /api/game/frame/ (this server, stored in cache) --(HTTP GET poll)-->
#   browser canvas game --(HTTP POST on finish)--> /api/game/save/ -> GameSession
#
# The bridge script re-uses the same serial parsing / COP maths as
# tkinter_app.py, just without the GUI, so a child's foot position on the
# mat becomes a moving dot in the browser in near real time.
# ---------------------------------------------------------------------------

GAME_FRAME_CACHE_TIMEOUT = 5  # seconds - if the bridge stops posting, frame goes stale fast

# Global process tracker for game mat bridge processes
BRIDGE_PROCESSES = {}
BRIDGE_PORTS = {}


def get_available_ports():
    """Retrieve list of connected serial COM ports."""
    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        return [
            {
                'device': p.device,
                'description': f"{p.device} - {p.description}" if p.description and p.description != p.device else p.device
            }
            for p in ports
        ]
    except Exception:
        return []


def get_serial_ports(request):
    """API endpoint returning available serial / COM ports."""
    ports = get_available_ports()
    return JsonResponse({'success': True, 'ports': ports})


def game_page(request, patient_id):
    """Render the 'Collect the Stars' game for a given patient."""
    patient = get_object_or_404(Patient, patient_id=patient_id)
    ports = get_available_ports()
    selected_port = request.GET.get('port', 'auto')
    if selected_port == 'auto' and ports:
        for p in ports:
            dev = p.get('device', '').lower()
            desc = p.get('description', '').lower()
            if 'usb' in dev or 'cp210' in desc or 'ch340' in desc or 'uart' in desc:
                selected_port = p['device']
                break
    return render(request, 'pressure_app/game.html', {
        'patient': patient,
        'ports': ports,
        'selected_port': selected_port
    })


@csrf_exempt
def api_start_game_bridge(request, patient_id):
    """
    POST: Start or restart game_bridge.py for patient_id on specified COM port.
    Body: {"port": "COM3"} or {"port": "auto"}
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)

    try:
        port = None
        if request.body:
            try:
                data = json.loads(request.body)
                port = data.get('port')
            except Exception:
                pass

        if not port:
            port = request.GET.get('port')

        patient_key = str(patient_id)
        target_port = port if port else 'auto'

        # Check if this exact patient is already running on the requested port
        old_proc = BRIDGE_PROCESSES.get(patient_key)
        curr_port = BRIDGE_PORTS.get(patient_key)
        if old_proc and old_proc.poll() is None:
            if curr_port == target_port or (target_port == 'auto' and curr_port):
                return JsonResponse({
                    'success': True,
                    'patient_id': patient_id,
                    'port': curr_port,
                    'message': f"Mat bridge already running on {curr_port}"
                })

        # Terminate any running bridge processes on the system to prevent serial port conflict
        for pkey, proc in list(BRIDGE_PROCESSES.items()):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=0.6)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        BRIDGE_PROCESSES.clear()
        BRIDGE_PORTS.clear()

        # Also kill any orphaned game_bridge processes
        try:
            subprocess.run(["pkill", "-f", "game_bridge.py"], capture_output=True)
            time.sleep(0.3)
        except Exception:
            pass

        base_dir = Path(__file__).parent.parent
        script_path = base_dir / "pressure_app" / "game_bridge.py"

        # Determine python executable: prefer virtualenv python
        py_bin = sys.executable
        venv_py = base_dir / ".venv" / "bin" / "python"
        if venv_py.exists():
            py_bin = str(venv_py)

        cmd = [py_bin, "-u", str(script_path), patient_key]
        if target_port and target_port != 'auto':
            cmd.append(str(target_port))

        startupinfo = None
        if sys.platform == "win32" and hasattr(subprocess, 'STARTUPINFO'):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        log_path = base_dir / "pressure_app" / f"bridge_{patient_key}.log"
        log_file = open(log_path, "w")

        proc = subprocess.Popen(
            cmd,
            startupinfo=startupinfo,
            stdout=log_file,
            stderr=subprocess.STDOUT
        )

        BRIDGE_PROCESSES[patient_key] = proc
        BRIDGE_PORTS[patient_key] = target_port

        # Wait briefly to confirm it didn't exit immediately with an error
        time.sleep(0.4)
        if proc.poll() is not None:
            log_file.close()
            with open(log_path, "r") as f:
                err_text = f.read()
            return JsonResponse({
                'success': False,
                'error': f"Bridge failed to start: {err_text.strip()}"
            }, status=500)

        return JsonResponse({
            'success': True,
            'patient_id': patient_id,
            'port': target_port,
            'message': f"Mat bridge started on {target_port}"
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def api_mat_recalibrate(request, patient_id):
    """Trigger zero-baseline calibration on the ESP32 matrix by creating a command signal."""
    base_dir = Path(__file__).parent.parent
    cmd_file = base_dir / "pressure_app" / f"cmd_{patient_id}.txt"
    try:
        with open(cmd_file, "w") as f:
            f.write("c")
        return JsonResponse({'success': True, 'message': 'Mat zero recalibration signal sent'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def api_stop_game_bridge(request, patient_id):
    """Stop running game bridge for patient_id."""
    patient_key = str(patient_id)
    proc = BRIDGE_PROCESSES.get(patient_key)
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        BRIDGE_PROCESSES.pop(patient_key, None)
        BRIDGE_PORTS.pop(patient_key, None)
    try:
        subprocess.run(["pkill", "-f", "game_bridge.py"], capture_output=True)
    except Exception:
        pass
    return JsonResponse({'success': True, 'patient_id': patient_id})


@csrf_exempt
def api_game_frame(request, patient_id):
    """
    POST: called by game_bridge.py with the latest COP/pressure reading.
          body: {"x": <0-19>, "y": <0-19>, "left_pct": .., "right_pct": .., "total_pressure": ..}
    GET:  polled by the browser game to get the latest reading for this patient.
    """
    cache_key = f'game_frame_{patient_id}'

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            frame = {
                'x': data.get('x'),
                'y': data.get('y'),
                'left_pct': data.get('left_pct', 50.0),
                'right_pct': data.get('right_pct', 50.0),
                'total_pressure': data.get('total_pressure', 0),
                'peak_pressure': data.get('peak_pressure', 0),
                'active_count': data.get('active_count', 0),
                'matrix': data.get('matrix', [0] * 20),
                'touching': data.get('touching', False),
                'port': data.get('port'),
                'server_time': time.time(),
            }
            cache.set(cache_key, frame, timeout=GAME_FRAME_CACHE_TIMEOUT)
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    # GET
    frame = cache.get(cache_key)
    if not frame:
        return JsonResponse({'connected': False})
    frame['connected'] = True
    return JsonResponse(frame)


@csrf_exempt
def api_game_save(request, patient_id):
    """Save a completed game session's results/path so a doctor can review it."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)

    try:
        patient = get_object_or_404(Patient, patient_id=patient_id)
        data = json.loads(request.body)

        session = GameSession.objects.create(
            patient=patient,
            game_type='collect_the_stars',
            duration_seconds=data.get('duration_seconds', 0.0),
            difficulty=data.get('difficulty', 'easy'),
            target_count=data.get('target_count', 0),
            stars_collected=data.get('stars_collected', 0),
            score=data.get('score', 0),
            path_data=data.get('path_data', []),
            star_events=data.get('star_events', []),
        )

        return JsonResponse({'success': True, 'session_id': session.id})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def get_patient_game_sessions(request, patient_id):
    """Small JSON history feed a doctor's dashboard can pull adherence stats from."""
    patient = get_object_or_404(Patient, patient_id=patient_id)
    sessions = patient.game_sessions.order_by('-started_at')[:50]
    return JsonResponse({
        'patient_id': patient.patient_id,
        'sessions': [
            {
                'id': s.id,
                'started_at': s.started_at.isoformat(),
                'duration_seconds': s.duration_seconds,
                'difficulty': s.difficulty,
                'target_count': s.target_count,
                'stars_collected': s.stars_collected,
                'score': s.score,
            }
            for s in sessions
        ]
    })