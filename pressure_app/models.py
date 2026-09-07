from django.db import models
from django.contrib.auth.models import AbstractUser

class Doctor(AbstractUser):
    license_number = models.CharField(max_length=50, unique=True)
    hospital = models.CharField(max_length=100)
    specialization = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.get_full_name()} ({self.license_number})"

class Patient(models.Model):
    name = models.CharField(max_length=100)
    patient_id = models.CharField(max_length=20, unique=True)
    contact_info = models.CharField(max_length=100)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    
    def __str__(self):
        return f"{self.name} ({self.patient_id})"

class PressureReading(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='readings')
    timestamp = models.DateTimeField(auto_now_add=True)
    pressure_data = models.JSONField()  # Stores the 20x20 pressure array
    displacement_x = models.FloatField(default=0.0)
    displacement_y = models.FloatField(default=0.0)
    movement_status = models.CharField(max_length=100, blank=True)
    session_notes = models.TextField(blank=True)
    is_analysis_complete = models.BooleanField(default=False)
    
    def __str__(self):
        return f"Reading for {self.patient.name} at {self.timestamp}"

class PatientVideo(models.Model):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='videos')
    video_file = models.FileField(upload_to='patient_videos/')
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"Video for {self.patient.name} at {self.timestamp}"


class GameSession(models.Model):
    """A single 'Collect the Stars' play session, recorded like a therapy session."""
    GAME_TYPES = [
        ('collect_the_stars', 'Collect the Stars'),
    ]

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='game_sessions')
    game_type = models.CharField(max_length=50, choices=GAME_TYPES, default='collect_the_stars')
    started_at = models.DateTimeField(auto_now_add=True)
    duration_seconds = models.FloatField(default=0.0)
    difficulty = models.CharField(max_length=20, default='easy')  # easy / medium / hard
    target_count = models.PositiveIntegerField(default=5)
    stars_collected = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=0)
    # List of {x, y, t} COP samples captured during play - reuses the same
    # grid coordinate space as PressureReading so it can be plotted the same way.
    path_data = models.JSONField(default=list, blank=True)
    # List of {index, x, y, t, time_to_reach} - one entry per star collected.
    star_events = models.JSONField(default=list, blank=True)
    session_notes = models.TextField(blank=True)

    def __str__(self):
        return f"Game session for {self.patient.name} at {self.started_at} ({self.stars_collected}/{self.target_count})"