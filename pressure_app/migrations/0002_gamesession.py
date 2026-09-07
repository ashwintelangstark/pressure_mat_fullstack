import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pressure_app', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GameSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('game_type', models.CharField(choices=[('collect_the_stars', 'Collect the Stars')], default='collect_the_stars', max_length=50)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('duration_seconds', models.FloatField(default=0.0)),
                ('difficulty', models.CharField(default='easy', max_length=20)),
                ('target_count', models.PositiveIntegerField(default=5)),
                ('stars_collected', models.PositiveIntegerField(default=0)),
                ('score', models.PositiveIntegerField(default=0)),
                ('path_data', models.JSONField(blank=True, default=list)),
                ('star_events', models.JSONField(blank=True, default=list)),
                ('session_notes', models.TextField(blank=True)),
                ('patient', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='game_sessions', to='pressure_app.patient')),
            ],
        ),
    ]
