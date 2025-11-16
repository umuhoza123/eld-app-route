from rest_framework.decorators import api_view
from rest_framework.response import Response
from datetime import datetime, timedelta
import requests
import math
import traceback
import time

# Global cache for geocoded addresses
GEOCODE_CACHE = {}

@api_view(['POST'])
def calculate_route(request):
    try:
        data = request.data
        print(f"\n{'='*60}")
        print(f"📥 RECEIVED REQUEST")
        print(f"{'='*60}")
        
        current_location = data.get('current_location')
        pickup_location = data.get('pickup_location')
        dropoff_location = data.get('dropoff_location')
        end_location = data.get('end_location', current_location)
        current_cycle_used = float(data.get('current_cycle_used', 0))
        
        if not end_location or end_location.strip() == '':
            end_location = current_location
            print(f"ℹ️  Empty end_location, using current_location: {end_location}")
        
        print(f"\n📍 Locations:")
        print(f"  Start: {current_location}")
        print(f"  Pickup: {pickup_location}")
        print(f"  Dropoff: {dropoff_location}")
        print(f"  End: {end_location}")
        print(f"  Cycle used: {current_cycle_used} hours")
        
        # HOS Rules
        MAX_DRIVING_HOURS = 11
        REQUIRED_BREAK_AFTER = 8
        BREAK_DURATION = 0.5
        OFF_DUTY_REQUIRED = 10
        
        print(f"\n🔄 Starting route calculation...")
        route_data = get_complete_route(current_location, pickup_location, dropoff_location, end_location)
        
        if not route_data:
            error_msg = 'Could not calculate route - geocoding or routing failed'
            print(f"❌ ERROR: {error_msg}")
            return Response({'error': error_msg}, status=400)
        
        print(f"✅ Route calculated successfully")
        
        total_distance = route_data['total_distance']
        total_duration = route_data['total_duration']
        
        print(f"\n📊 Route Summary:")
        print(f"  Total Distance: {total_distance:.1f} miles")
        print(f"  Total Duration: {total_duration:.1f} hours")
        print(f"  Segments: {len(route_data['segments'])}")
        
        # Calculate stops
        print(f"\n🛑 Calculating stops...")
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
        
        print(f"✅ Generated {len(stops)} stops")
        
        eld_logs = generate_eld_logs(stops, total_distance, current_cycle_used)
        
        response_data = {
            'route': {
                'total_distance': round(total_distance, 2),
                'total_duration': round(total_duration, 2),
                'segments': route_data['segments'],
                'main_points': route_data['main_points']
            },
            'stops': stops,
            'eld_logs': eld_logs,
            'total_distance': round(total_distance, 2),
            'total_duration': round(total_duration, 2)
        }
        
        print(f"\n✅ SUCCESS - Sending response")
        print(f"{'='*60}\n")
        return Response(response_data)
        
    except Exception as e:
        print(f"\n❌ CRITICAL ERROR in calculate_route:")
        print(f"Error: {str(e)}")
        traceback.print_exc()
        return Response({'error': f'Server error: {str(e)}'}, status=500)


def get_complete_route(start, pickup, dropoff, end):
    """Get complete route with 4 main points using cached geocoding"""
    try:
        print(f"\n🌍 GEOCODING ADDRESSES (with caching)")
        print(f"-" * 40)
        
        # Collect unique addresses to geocode
        addresses = {
            'start': start,
            'pickup': pickup,
            'dropoff': dropoff,
            'end': end
        }
        
        coords = {}
        
        # Geocode each unique address (with caching)
        unique_addresses = set(addresses.values())
        print(f"  Need to geocode {len(unique_addresses)} unique location(s)")
        
        for addr in unique_addresses:
            if addr in GEOCODE_CACHE:
                print(f"  📦 Using cached: {addr} -> {GEOCODE_CACHE[addr]}")
                coords[addr] = GEOCODE_CACHE[addr]
            else:
                print(f"  🌐 Geocoding: {addr}")
                result = geocode_address(addr)
                if result:
                    GEOCODE_CACHE[addr] = result
                    coords[addr] = result
                    print(f"  ✅ Success: {addr} -> {result}")
                    # Only wait if we actually made a request
                    if len(unique_addresses) > 1:
                        time.sleep(1.2)  # Wait 1.2 seconds between requests
                else:
                    print(f"  ❌ Failed: {addr}")
                    return None
        
        # Map coordinates to each location
        start_coords = coords[start]
        pickup_coords = coords[pickup]
        dropoff_coords = coords[dropoff]
        end_coords = coords[end]
        
        print(f"\n🛣️  GETTING ROUTE SEGMENTS")
        print(f"-" * 40)
        
        # Get route segments
        print(f"  Segment 1: START → PICKUP")
        segment1 = get_route_segment(start_coords, pickup_coords, "to_pickup")
        if not segment1:
            print(f"  ⚠️  OSRM failed, using fallback")
            return get_fallback_complete_route(start_coords, pickup_coords, dropoff_coords, end_coords, start, pickup, dropoff, end)
        print(f"  ✅ Segment 1: {segment1['distance']:.1f} miles")
        
        print(f"  Segment 2: PICKUP → DROPOFF")
        segment2 = get_route_segment(pickup_coords, dropoff_coords, "delivery")
        if not segment2:
            print(f"  ⚠️  OSRM failed, using fallback")
            return get_fallback_complete_route(start_coords, pickup_coords, dropoff_coords, end_coords, start, pickup, dropoff, end)
        print(f"  ✅ Segment 2: {segment2['distance']:.1f} miles")
        
        print(f"  Segment 3: DROPOFF → END")
        segment3 = get_route_segment(dropoff_coords, end_coords, "return")
        if not segment3:
            print(f"  ⚠️  OSRM failed, using fallback")
            return get_fallback_complete_route(start_coords, pickup_coords, dropoff_coords, end_coords, start, pickup, dropoff, end)
        print(f"  ✅ Segment 3: {segment3['distance']:.1f} miles")
        
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
        print(f"❌ Error in get_complete_route: {e}")
        traceback.print_exc()
        return None


def get_route_segment(from_coords, to_coords, segment_type):
    """Get a single route segment using OSRM"""
    try:
        waypoints = f"{from_coords[1]},{from_coords[0]};{to_coords[1]},{to_coords[0]}"
        osrm_url = f"http://router.project-osrm.org/route/v1/driving/{waypoints}"
        
        params = {
            'overview': 'full',
            'geometries': 'geojson'
        }
        
        print(f"    🌐 Calling OSRM...")
        response = requests.get(osrm_url, params=params, timeout=25)
        data = response.json()
        
        if data.get('code') == 'Ok' and data.get('routes'):
            route = data['routes'][0]
            
            geometry_coords = route['geometry']['coordinates']
            geometry = [[coord[1], coord[0]] for coord in geometry_coords]
            
            distance_miles = route['distance'] * 0.000621371
            duration_hours = route['duration'] / 3600
            
            print(f"    ✅ Got {len(geometry)} waypoints, {distance_miles:.1f} miles")
            
            return {
                'type': segment_type,
                'geometry': geometry,
                'distance': distance_miles,
                'duration': duration_hours,
                'start': from_coords,
                'end': to_coords
            }
        else:
            print(f"    ❌ OSRM returned: {data.get('code', 'Unknown error')}")
            return None
            
    except requests.Timeout:
        print(f"    ⏱️  OSRM timeout after 25 seconds")
        return None
    except Exception as e:
        print(f"    ❌ OSRM error: {e}")
        return None


def get_fallback_complete_route(start_coords, pickup_coords, dropoff_coords, end_coords, start, pickup, dropoff, end):
    """Fallback route calculation if OSRM fails"""
    print(f"\n⚠️  USING FALLBACK ROUTING (straight lines)")
    
    dist1 = calculate_distance(start_coords, pickup_coords) * 1.3
    dist2 = calculate_distance(pickup_coords, dropoff_coords) * 1.3
    dist3 = calculate_distance(dropoff_coords, end_coords) * 1.3
    
    total_distance = dist1 + dist2 + dist3
    
    print(f"  Segment 1: {dist1:.1f} miles")
    print(f"  Segment 2: {dist2:.1f} miles")
    print(f"  Segment 3: {dist3:.1f} miles")
    
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
            'start': {'coords': start_coords, 'name': start, 'type': 'start'},
            'pickup': {'coords': pickup_coords, 'name': pickup, 'type': 'pickup'},
            'dropoff': {'coords': dropoff_coords, 'name': dropoff, 'type': 'dropoff'},
            'end': {'coords': end_coords, 'name': end, 'type': 'end'}
        }
    }


def calculate_stops_on_route(segments, total_distance, current_cycle_used,
                             start_location, pickup_location, dropoff_location, end_location,
                             MAX_DRIVING_HOURS, REQUIRED_BREAK_AFTER, 
                             BREAK_DURATION, OFF_DUTY_REQUIRED):
    """Calculate stops along the route"""
    stops = []
    current_hours = current_cycle_used
    hours_driven = 0
    distance_since_fuel = 0
    distance_covered = 0
    
    all_geometry = []
    for segment in segments:
        all_geometry.extend(segment['geometry'])
    
    # START
    stops.append({
        'type': 'Start',
        'location': start_location,
        'duration': 0,
        'distance_from_start': 0,
        'coordinates': segments[0]['start']
    })
    
    # PICKUP
    to_pickup_distance = segments[0]['distance']
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
    
    # Main delivery segment
    delivery_distance = segments[1]['distance']
    segment_end = distance_covered + delivery_distance
    
    while distance_covered < segment_end - 50:
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
        
        drive_hours = min(2, (segment_end - distance_covered) / 60)
        drive_distance = drive_hours * 60
        
        distance_covered += drive_distance
        current_hours += drive_hours
        hours_driven += drive_hours
        distance_since_fuel += drive_distance
    
    # DROPOFF
    distance_covered = to_pickup_distance + delivery_distance
    stops.append({
        'type': 'Dropoff',
        'location': dropoff_location,
        'duration': 1,
        'distance_from_start': distance_covered,
        'coordinates': segments[1]['end']
    })
    
    # END
    stops.append({
        'type': 'End',
        'location': end_location,
        'duration': 0,
        'distance_from_start': total_distance,
        'coordinates': segments[2]['end']
    })
    
    return stops


def get_coordinates_at_distance(geometry, target_distance, total_distance):
    """Get coordinates at a specific distance along the route"""
    if not geometry or len(geometry) < 2:
        return geometry[0] if geometry else [0, 0]
    
    target_index = int((target_distance / total_distance) * (len(geometry) - 1))
    target_index = max(0, min(target_index, len(geometry) - 1))
    
    return geometry[target_index]


def geocode_address(address):
    """Geocode address using Nominatim - returns coords or None"""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            'q': address,
            'format': 'json',
            'limit': 1,
            'addressdetails': 1
        }
        headers = {
            'User-Agent': 'ELD-Trucking-App/1.0',
            'Accept-Language': 'en'
        }
        
        # Longer timeout
        response = requests.get(url, params=params, headers=headers, timeout=20)
        
        if response.status_code == 200:
            data = response.json()
            if data and len(data) > 0:
                coords = [float(data[0]['lat']), float(data[0]['lon'])]
                return coords
        
        print(f"    ⚠️  No results or error for: {address}")
        return None
            
    except requests.Timeout:
        print(f"    ⏱️  Timeout (20s) for: {address}")
        return None
    except Exception as e:
        print(f"    ❌ Error for {address}: {e}")
        return None


def calculate_distance(coord1, coord2):
    """Calculate distance between coordinates in miles"""
    lat1, lon1 = coord1
    lat2, lon2 = coord2
    
    R = 3959
    
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * 
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


def generate_eld_logs(stops, total_distance, initial_cycle):
    """Generate ELD logs"""
    logs = []
    current_time = datetime.now()
    current_hours = initial_cycle
    
    for stop in stops:
        if stop['type'] not in ['Start', 'End']:
            log_entry = {
                'date': current_time.strftime('%Y-%m-%d'),
                'time': current_time.strftime('%H:%M'),
                'status': get_status_from_stop(stop['type']),
                'location': stop.get('location', 'En Route'),
                'hours_driven': round(current_hours, 1),
                'remarks': stop['type']
            }
            logs.append(log_entry)
        
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