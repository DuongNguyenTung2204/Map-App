import requests
import logging
import json

# Thiết lập logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def query_overpass_around(lat, lon, radius=50):
    """
    Truy vấn Overpass API để lấy các đoạn đường (ways) trong bán kính radius quanh tọa độ (lat, lon).
    """
    overpass_url = "http://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json];
    way(around:{radius},{lat},{lon})["highway"];
    out body;
    >;
    out skel qt;
    """
    try:
        response = requests.post(overpass_url, data=overpass_query, timeout=10)
        response.raise_for_status()
        data = response.json()
        ways = []
        nodes = {element['id']: (element['lat'], element['lon']) for element in data['elements'] if element['type'] == 'node'}
        
        for element in data['elements']:
            if element['type'] == 'way':
                way_coords = []
                for node_id in element['nodes']:
                    if node_id in nodes:
                        way_coords.append(nodes[node_id])
                if way_coords:
                    ways.append({
                        'id': element['id'],
                        'coords': way_coords,
                        'tags': element.get('tags', {})
                    })
        return {'ways': ways}
    except requests.RequestException as e:
        logging.error(f"Lỗi khi truy vấn Overpass API: {e}")
        raise

def distance_point_to_segment(px, py, x1, y1, x2, y2):
    """
    Tính khoảng cách từ điểm (px, py) đến đoạn thẳng từ (x1, y1) đến (x2, y2).
    """
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    projection_x = x1 + t * dx
    projection_y = y1 + t * dy
    return ((px - projection_x) ** 2 + (py - projection_y) ** 2) ** 0.5

def find_nearest_way(lat, lon):
    """
    Tìm đoạn đường gần nhất với tọa độ (lat, lon).
    """
    try:
        data = query_overpass_around(lat, lon)
        ways = data['ways']
        min_distance = float('inf')
        nearest_way_id = None
        
        for way in ways:
            coords = way['coords']
            for i in range(len(coords) - 1):
                node1_lat, node1_lon = coords[i]
                node2_lat, node2_lon = coords[i + 1]
                distance = distance_point_to_segment(lat, lon, node1_lat, node1_lon, node2_lat, node2_lon)
                if distance < min_distance:
                    min_distance = distance
                    nearest_way_id = way['id']
        
        if nearest_way_id:
            logging.debug(f"Nearest way: ID={nearest_way_id}, Distance={min_distance}")
            return nearest_way_id
        else:
            logging.warning("Không tìm thấy đường trong khu vực")
            return None
    except Exception as e:
        logging.error(f"Lỗi khi tìm đoạn đường: {e}")
        raise

def get_way_coordinates(way_id):
    """
    Lấy tọa độ của các node thuộc đoạn đường (way) theo way_id.
    """
    overpass_url = "http://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json];
    way({way_id});
    out body;
    >;
    out skel qt;
    """
    try:
        response = requests.post(overpass_url, data=overpass_query, timeout=10)
        response.raise_for_status()
        data = response.json()
        logging.debug(f"Overpass response for way_id={way_id}: {json.dumps(data, indent=2)}")
        
        nodes = {element['id']: [element['lat'], element['lon']] for element in data['elements'] if element['type'] == 'node'}
        way = next((element for element in data['elements'] if element['type'] == 'way' and element['id'] == int(way_id)), None)
        
        if not way:
            logging.warning(f"Không tìm thấy way_id={way_id}")
            return []
        
        coords = []
        for node_id in way['nodes']:
            if node_id in nodes:
                coords.append(nodes[node_id])
        logging.debug(f"Lấy được {len(coords)} tọa độ cho way_id={way_id}")
        return coords
    except requests.RequestException as e:
        logging.error(f"Lỗi khi lấy tọa độ way_id={way_id}: {e}")
        return []
    except ValueError as e:
        logging.error(f"Lỗi xử lý way_id={way_id}: {e}")
        return []