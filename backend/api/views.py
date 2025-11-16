from rest_framework.decorators import api_view
from rest_framework.response import Response
from datetime import datetime, timedelta
import requests
import math

@api_view(['POST'])
def calculate_route(request):
    data = request.data
    current_location = data.get('current_location')  # START point
    pickup_location = data.get('pickup_location')
    dropoff_location = data.get('dropoff_location')
    end_location = data.get('end_location', current_location)  # END point (defaults to current if not provided)
    current_cycle_used = float(data.get('current_cycle_used', 0))
    
    # HOS Rules
    MAX_DRIVING_HOURS = 11
    MAX_DUTY_HOURS = 14
    REQUIRED_BREAK_AFTER = 8
    BREAK_DURATION = 0.5
    OFF_DUTY_REQUIRED = 10
    MAX_CYCLE_HOURS = 70
    
    # Get route data with actual road geometry for all 4 points
    route_data = get_complete_route(current_location, pickup_location, dropoff_location, end_location)
    
    if not route_data:
        return Response({'error': 'Could not calculate route'}, status=400)
    
    total_distance = route_data['total_distance']  # in miles
    total_duration = route_data['total_duration']  # in hours
    
    # Calculate stops with coordinates from actual route
    stops = calculate_stops_on_route(
        route_data['segments'],
        total_distance,
        current_cycle_used,
        current_location,
        pickup_location,
        dropoff_location,
        end_location,
        MAX_DRIVING_HOURS,
        REQUIRED_BREAK_AFTER,
        BREAK_DURATION,
        OFF_DUTY_REQUIRED
    )
    
    # Generate ELD logs
    eld_logs = generate_eld_logs(stops, total_distance, current_cycle_used)
    
    return Response({
        'route': {
            'total_distance': total_distance,
            'total_duration': total_duration,
            'segments': route_data['segments'],  # Multiple route segments
            'main_points': route_data['main_points']  # Start, Pickup, Dropoff, End
        },
        'stops': stops,
        'eld_logs': eld_logs,
        'total_distance': round(total_distance, 2),
        'total_duration': round(total_duration, 2)
    })


def get_complete_route(start, pickup, dropoff, end):
    """
    Get complete route with 4 main points:
    START → PICKUP → DROPOFF → END
    Returns segments for each part of the journey
    """
    try:
        # Geocode all addresses
        start_coords = geocode_address(start)
        pickup_coords = geocode_address(pickup)
        dropoff_coords = geocode_address(dropoff)
        end_coords = geocode_address(end)
        
        if not all([start_coords, pickup_coords, dropoff_coords, end_coords]):
            return None
        
        # Get three route segments:
        # Segment 1: START → PICKUP
        segment1 = get_route_segment(start_coords, pickup_coords, "to_pickup")
        
        # Segment 2: PICKUP → DROPOFF (main delivery route)
        segment2 = get_route_segment(pickup_coords, dropoff_coords, "delivery")
        
        # Segment 3: DROPOFF → END
        segment3 = get_route_segment(dropoff_coords, end_coords, "return")
        
        if not all([segment1, segment2, segment3]):
            return get_fallback_complete_route(start_coords, pickup_coords, dropoff_coords, end_coords)
        
        # Calculate total distance and duration
        total_distance = segment1['distance'] + segment2['distance'] + segment3['distance']
        total_duration = segment1['duration'] + segment2['duration'] + segment3['duration']
        
        return {
            'total_distance': total_distance,
            'total_duration': total_duration,
            'segments': [segment1, segment2, segment3],
            'main_points': {
                'start': {'coords': start_coords, 'name': start, 'type': 'start'},
                'pickup': {'coords': pickup_coords, 'name': pickup, 'type': 'pickup'},
                'dropoff': {'coords': dropoff_coords, 'name': dropoff, 'type': 'dropoff'},
                'end': {'coords': end_coords, 'name': end, 'type': 'end'}
            }
        }
        
    except Exception as e:
        print(f"Routing error: {e}")
        return None


def get_route_segment(from_coords, to_coords, segment_type):
    """Get a single route segment using OSRM"""
    try:
        # OSRM uses [lon, lat] format
        waypoints = f"{from_coords[1]},{from_coords[0]};{to_coords[1]},{to_coords[0]}"
        
        osrm_url = f"http://router.project-osrm.org/route/v1/driving/{waypoints}"
        params = {
            'overview': 'full',
            'geometries': 'geojson',
            'steps': 'true'
        }
        
        response = requests.get(osrm_url, params=params, timeout=10)
        data = response.json()
        
        if data.get('code') == 'Ok' and data.get('routes'):
            route = data['routes'][0]
            
            # Extract geometry (convert from [lon, lat] to [lat, lon])
            geometry_coords = route['geometry']['coordinates']
            geometry = [[coord[1], coord[0]] for coord in geometry_coords]
            
            # Distance in meters, convert to miles
            distance_miles = route['distance'] * 0.000621371
            
            # Duration in seconds, convert to hours
            duration_hours = route['duration'] / 3600
            
            return {
                'type': segment_type,
                'geometry': geometry,
                'distance': distance_miles,
                'duration': duration_hours,
                'start': from_coords,
                'end': to_coords
            }
        else:
            return None
            
    except Exception as e:
        print(f"Segment routing error: {e}")
        return None


def get_fallback_complete_route(start_coords, pickup_coords, dropoff_coords, end_coords):
    """Fallback route calculation if API fails"""
    dist1 = calculate_distance(start_coords, pickup_coords) * 1.3
    dist2 = calculate_distance(pickup_coords, dropoff_coords) * 1.3
    dist3 = calculate_distance(dropoff_coords, end_coords) * 1.3
    
    total_distance = dist1 + dist2 + dist3
    
    return {
        'total_distance': total_distance,
        'total_duration': total_distance / 60,
        'segments': [
            {
                'type': 'to_pickup',
                'geometry': [start_coords, pickup_coords],
                'distance': dist1,
                'duration': dist1 / 60,
                'start': start_coords,
                'end': pickup_coords
            },
            {
                'type': 'delivery',
                'geometry': [pickup_coords, dropoff_coords],
                'distance': dist2,
                'duration': dist2 / 60,
                'start': pickup_coords,
                'end': dropoff_coords
            },
            {
                'type': 'return',
                'geometry': [dropoff_coords, end_coords],
                'distance': dist3,
                'duration': dist3 / 60,
                'start': dropoff_coords,
                'end': end_coords
            }
        ],
        'main_points': {
            'start': {'coords': start_coords, 'type': 'start'},
            'pickup': {'coords': pickup_coords, 'type': 'pickup'},
            'dropoff': {'coords': dropoff_coords, 'type': 'dropoff'},
            'end': {'coords': end_coords, 'type': 'end'}
        }
    }


def calculate_stops_on_route(segments, total_distance, current_cycle_used,
                             start_location, pickup_location, dropoff_location, end_location,
                             MAX_DRIVING_HOURS, REQUIRED_BREAK_AFTER, 
                             BREAK_DURATION, OFF_DUTY_REQUIRED):
    """Calculate stops along the actual route with all segments"""
    stops = []
    current_hours = current_cycle_used
    hours_driven = 0
    distance_since_fuel = 0
    distance_covered = 0
    
    # Combine all geometry points from segments
    all_geometry = []
    for segment in segments:
        all_geometry.extend(segment['geometry'])
    
    # Add START point
    stops.append({
        'type': 'Start',
        'location': start_location,
        'duration': 0,
        'distance_from_start': 0,
        'coordinates': segments[0]['start']
    })
    
    # Calculate distance to pickup
    to_pickup_distance = segments[0]['distance']
    
    # Add pickup stop
    stops.append({
        'type': 'Pickup',
        'location': pickup_location,
        'duration': 1,
        'distance_from_start': to_pickup_distance,
        'coordinates': segments[0]['end']
    })
    
    distance_covered = to_pickup_distance
    current_hours += segments[0]['duration']
    hours_driven += segments[0]['duration']
    distance_since_fuel += to_pickup_distance
    
    # Main delivery segment (Pickup → Dropoff)
    delivery_distance = segments[1]['distance']
    segment_end = distance_covered + delivery_distance
    
    while distance_covered < segment_end:
        # Check if fuel stop needed
        if distance_since_fuel >= 1000:
            coords = get_coordinates_at_distance(all_geometry, distance_covered, total_distance)
            stops.append({
                'type': 'Fuel Stop',
                'duration': 0.5,
                'distance_from_start': distance_covered,
                'coordinates': coords,
                'location': f"En Route ({distance_covered:.0f} mi)"
            })
            distance_since_fuel = 0
            current_hours += 0.5
        
        # Check if break needed
        if hours_driven >= REQUIRED_BREAK_AFTER:
            coords = get_coordinates_at_distance(all_geometry, distance_covered, total_distance)
            stops.append({
                'type': '30-min Break',
                'duration': BREAK_DURATION,
                'distance_from_start': distance_covered,
                'coordinates': coords,
                'location': f"En Route ({distance_covered:.0f} mi)"
            })
            hours_driven = 0
        
        # Check if daily rest needed
        if current_hours >= MAX_DRIVING_HOURS:
            coords = get_coordinates_at_distance(all_geometry, distance_covered, total_distance)
            stops.append({
                'type': '10-hour Rest',
                'duration': OFF_DUTY_REQUIRED,
                'distance_from_start': distance_covered,
                'coordinates': coords,
                'location': f"En Route ({distance_covered:.0f} mi)"
            })
            current_hours = 0
            hours_driven = 0
        
        # Drive segment
        drive_hours = min(2, (segment_end - distance_covered) / 60)
        drive_distance = drive_hours * 60
        
        distance_covered += drive_distance
        current_hours += drive_hours
        hours_driven += drive_hours
        distance_since_fuel += drive_distance
    
    # Add dropoff stop
    distance_covered = to_pickup_distance + delivery_distance
    stops.append({
        'type': 'Dropoff',
        'location': dropoff_location,
        'duration': 1,
        'distance_from_start': distance_covered,
        'coordinates': segments[1]['end']
    })
    
    # Return segment (Dropoff → End)
    return_distance = segments[2]['distance']
    segment_end = distance_covered + return_distance
    
    distance_covered += segments[2]['duration'] * 60
    current_hours += segments[2]['duration']
    hours_driven += segments[2]['duration']
    
    # Add END point
    stops.append({
        'type': 'End',
        'location': end_location,
        'duration': 0,
        'distance_from_start': total_distance,
        'coordinates': segments[2]['end']
    })
    
    return stops


def get_coordinates_at_distance(geometry, target_distance, total_distance):
    """Get coordinates at a specific distance along the route geometry"""
    if not geometry or len(geometry) < 2:
        return geometry[0] if geometry else [0, 0]
    
    target_index = int((target_distance / total_distance) * (len(geometry) - 1))
    target_index = max(0, min(target_index, len(geometry) - 1))
    
    return geometry[target_index]


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
        
        response = requests.get(url, params=params, headers=headers, timeout=5)
        data = response.json()
        
        if data:
            return [float(data[0]['lat']), float(data[0]['lon'])]
        return None
    except Exception as e:
        print(f"Geocoding error: {e}")
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
        if stop['type'] not in ['Start', 'End']:  # Don't log start/end as separate entries
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
        if 'Rest' not in stop['type'] and stop['type'] not in ['Start', 'End']:
            current_hours += stop['duration']
    
    return logs


def get_status_from_stop(stop_type):
    """Map stop type to ELD status"""
    if 'Rest' in stop_type:
        return 'OFF'
    elif 'Break' in stop_type:
        return 'SB'
    elif stop_type in ['Pickup', 'Dropoff', 'Start', 'End']:
        return 'ON'
    elif 'Fuel' in stop_type:
        return 'ON'
    else:
        return 'D'