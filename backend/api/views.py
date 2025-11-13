from rest_framework.decorators import api_view
from rest_framework.response import Response
from datetime import datetime, timedelta
import requests
import math

@api_view(['POST'])
def calculate_route(request):
    data = request.data
    current_location = data.get('current_location')
    pickup_location = data.get('pickup_location')
    dropoff_location = data.get('dropoff_location')
    current_cycle_used = float(data.get('current_cycle_used', 0))
    
    # HOS Rules
    MAX_DRIVING_HOURS = 11
    MAX_DUTY_HOURS = 14
    REQUIRED_BREAK_AFTER = 8
    BREAK_DURATION = 0.5
    OFF_DUTY_REQUIRED = 10
    MAX_CYCLE_HOURS = 70
    
    # Get route data using OpenRouteService (free API)
    route_data = get_route_coordinates(current_location, pickup_location, dropoff_location)
    
    if not route_data:
        return Response({'error': 'Could not calculate route'}, status=400)
    
    total_distance = route_data['distance']  # in miles
    total_duration = route_data['duration']  # in hours
    
    # Calculate stops
    stops = []
    current_hours = current_cycle_used
    remaining_distance = total_distance
    
    # Add pickup stop
    stops.append({
        'type': 'Pickup',
        'location': pickup_location,
        'duration': 1,
        'distance_from_start': 0
    })
    
    hours_driven = 0
    distance_since_fuel = 0
    
    while remaining_distance > 0:
        # Check if fuel stop needed
        if distance_since_fuel >= 1000:
            stops.append({
                'type': 'Fuel Stop',
                'duration': 0.5,
                'distance_from_start': total_distance - remaining_distance
            })
            distance_since_fuel = 0
        
        # Check if break needed
        if hours_driven >= REQUIRED_BREAK_AFTER:
            stops.append({
                'type': '30-min Break',
                'duration': BREAK_DURATION,
                'distance_from_start': total_distance - remaining_distance
            })
            hours_driven = 0
        
        # Check if daily rest needed
        if current_hours >= MAX_DRIVING_HOURS:
            stops.append({
                'type': '10-hour Rest',
                'duration': OFF_DUTY_REQUIRED,
                'distance_from_start': total_distance - remaining_distance
            })
            current_hours = 0
            hours_driven = 0
        
        # Drive segment
        drive_hours = min(2, remaining_distance / 60)  # Assume 60 mph average
        remaining_distance -= drive_hours * 60
        current_hours += drive_hours
        hours_driven += drive_hours
        distance_since_fuel += drive_hours * 60
    
    # Add dropoff stop
    stops.append({
        'type': 'Dropoff',
        'location': dropoff_location,
        'duration': 1,
        'distance_from_start': total_distance
    })
    
    # Generate ELD logs
    eld_logs = generate_eld_logs(stops, total_distance, current_cycle_used)
    
    return Response({
        'route': route_data,
        'stops': stops,
        'eld_logs': eld_logs,
        'total_distance': round(total_distance, 2),
        'total_duration': round(total_duration, 2)
    })


def get_route_coordinates(current, pickup, dropoff):
    """Get route using Nominatim for geocoding (free)"""
    try:
        # Geocode addresses
        current_coords = geocode_address(current)
        pickup_coords = geocode_address(pickup)
        dropoff_coords = geocode_address(dropoff)
        
        if not all([current_coords, pickup_coords, dropoff_coords]):
            return None
        
        # Calculate simple distance (straight line * 1.3 for road distance estimate)
        dist1 = calculate_distance(current_coords, pickup_coords)
        dist2 = calculate_distance(pickup_coords, dropoff_coords)
        total_distance = (dist1 + dist2) * 1.3  # Road factor
        
        # Estimate duration (average 60 mph)
        total_duration = total_distance / 60
        
        return {
            'distance': total_distance,
            'duration': total_duration,
            'coordinates': [current_coords, pickup_coords, dropoff_coords]
        }
    except Exception as e:
        print(f"Error: {e}")
        return None


def geocode_address(address):
    """Geocode address using Nominatim (free, no API key needed)"""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': address,
            'format': 'json',
            'limit': 1
        }
        headers = {'User-Agent': 'ELD-Trucking-App/1.0'}
        
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        
        if data:
            return [float(data[0]['lat']), float(data[0]['lon'])]
        return None
    except:
        return None


def calculate_distance(coord1, coord2):
    """Calculate distance between two coordinates in miles"""
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    
    R = 3959  # Earth radius in miles
    
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


def generate_eld_logs(stops, total_distance, initial_cycle):
    """Generate ELD log data"""
    logs = []
    current_time = datetime.now()
    current_hours = initial_cycle
    
    for i, stop in enumerate(stops):
        log_entry = {
            'date': current_time.strftime('%Y-%m-%d'),
            'time': current_time.strftime('%H:%M'),
            'status': get_status_from_stop(stop['type']),
            'location': stop.get('location', 'En Route'),
            'hours_driven': round(current_hours, 1),
            'remarks': stop['type']
        }
        logs.append(log_entry)
        
        # Update time for next entry
        current_time += timedelta(hours=stop['duration'])
        if 'Rest' not in stop['type']:
            current_hours += stop['duration']
    
    return logs


def get_status_from_stop(stop_type):
    """Map stop type to ELD status"""
    if 'Rest' in stop_type:
        return 'OFF'
    elif 'Break' in stop_type:
        return 'SB'  # Sleeper Berth
    elif stop_type in ['Pickup', 'Dropoff']:
        return 'ON'
    elif 'Fuel' in stop_type:
        return 'ON'
    else:
        return 'D'  # Driving