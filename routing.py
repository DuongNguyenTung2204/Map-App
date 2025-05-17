import mysql.connector
import networkx as nx
import math
import json
import heapq
import logging
from math import radians, sin, cos, sqrt, atan2

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def test_db_connection(db_config):
    try:
        conn = mysql.connector.connect(**db_config)
        conn.close()
        logging.info("Kết nối CSDL thành công")
        return True
    except mysql.connector.Error as err:
        logging.error(f"Lỗi kết nối CSDL: {err}")
        return False

def haversine(lon1, lat1, lon2, lat2):
    R = 6371.0
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distance = R * c
    return distance

def get_blocked_ways(db_config):
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        query = "SELECT way_id FROM traffic_changes WHERE traffic_type IN ('blocked', 'closed')"
        cursor.execute(query)
        blocked_ways = {str(row['way_id']) for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        logging.info(f"Tìm thấy {len(blocked_ways)} way bị tắc hoặc bị cấm")
        return blocked_ways
    except Exception as e:
        logging.error(f"Lỗi truy vấn traffic_changes: {str(e)}")
        return set()

def apply_traffic_penalties(G, db_config, penalty_factors={'slow': 2, 'blocked': 10, 'closed': 1000}):
    G_modified = G.copy()
    blocked_edges = 0
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        for u, v, data in G_modified.edges(data=True):
            try:
                tags = json.loads(data['tags'])
                way_id = tags.get('id')
                query = "SELECT traffic_type FROM traffic_changes WHERE way_id = %s"
                cursor.execute(query, (way_id,))
                result = cursor.fetchone()
                if result and result['traffic_type'] in penalty_factors:
                    original_weight = data['weight']
                    penalty = penalty_factors[result['traffic_type']]
                    data['weight'] = data['weight'] * penalty
                    blocked_edges += 1
                    logging.info(f"Áp dụng phạt cho cạnh ({u}, {v}), way_id: {way_id}, traffic_type: {result['traffic_type']}, trọng số cũ: {original_weight:.3f}, trọng số mới: {data['weight']:.3f}")
            except (json.JSONDecodeError, KeyError):
                continue
        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        logging.error(f"Lỗi truy vấn CSDL trong apply_traffic_penalties: {err}")
    logging.info(f"Áp dụng phạt cho {blocked_edges} cạnh")
    return G_modified

def project_to_edge(G, u, v, target_lat, target_lng):
    x1, y1 = G.nodes[u]['lat'], G.nodes[u]['lon']
    x2, y2 = G.nodes[v]['lat'], G.nodes[v]['lon']
    x, y = target_lat, target_lng

    if x1 == x2 and y1 == y2:
        dist = haversine(y, x, y1, x1)
        return x1, y1, dist, 0

    dx = x2 - x1
    dy = y2 - y1
    length_sq = dx**2 + dy**2
    t = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / length_sq))

    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    dist = haversine(y, x, proj_y, proj_x)

    return proj_x, proj_y, dist, t

def snap_to_edge(G, target_lat, target_lng, max_distance_km=0.5):
    min_dist = float('inf')
    closest_edge = None
    closest_point = None
    closest_t = None

    for u, v in G.edges():
        proj_lat, proj_lon, dist, t = project_to_edge(G, u, v, target_lat, target_lng)
        if dist < min_dist and dist <= max_distance_km:
            min_dist = dist
            closest_edge = (u, v)
            closest_point = (proj_lat, proj_lon)
            closest_t = t

    if closest_edge is None:
        logging.warning("Không tìm thấy cạnh nào trong khoảng cách tối đa")
        return None, G

    u, v = closest_edge
    if closest_t < 0.01:
        logging.info(f"Snap đến node hiện có {u}")
        return u, G
    elif closest_t > 0.99:
        logging.info(f"Snap đến node hiện có {v}")
        return v, G

    G_modified = G.copy()
    new_node = max(G.nodes()) + 1
    G_modified.add_node(new_node, lat=closest_point[0], lon=closest_point[1])

    edge_data = G.get_edge_data(u, v)
    weight = edge_data['weight'] * closest_t
    G_modified.add_edge(u, new_node, weight=weight, tags=edge_data['tags'])

    weight = edge_data['weight'] * (1 - closest_t)
    G_modified.add_edge(new_node, v, weight=weight, tags=edge_data['tags'])

    if G_modified.has_edge(v, u):
        edge_data = G.get_edge_data(v, u)
        weight = edge_data['weight'] * (1 - closest_t)
        G_modified.add_edge(v, new_node, weight=weight, tags=edge_data['tags'])
        weight = edge_data['weight'] * closest_t
        G_modified.add_edge(new_node, u, weight=weight, tags=edge_data['tags'])

    G_modified.remove_edge(u, v)
    if G_modified.has_edge(v, u):
        G_modified.remove_edge(v, u)

    logging.info(f"Tạo node ảo {new_node} tại ({closest_point[0]}, {closest_point[1]})")
    return new_node, G_modified

def a_star_path(G, source, target, end_lat, end_lng):
    open_set = [(0, source, [source])]
    heapq.heapify(open_set)
    closed_set = set()
    g_score = {source: 0}
    f_score = {source: haversine(G.nodes[source]['lon'], G.nodes[source]['lat'], end_lng, end_lat)}

    while open_set:
        _, current, path = heapq.heappop(open_set)

        if current == target:
            return path

        if current in closed_set:
            continue

        closed_set.add(current)

        for neighbor in G.neighbors(current):
            if neighbor in closed_set:
                continue

            tentative_g_score = g_score[current] + G[current][neighbor]['weight']

            if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                g_score[neighbor] = tentative_g_score
                f_score[neighbor] = tentative_g_score + haversine(
                    G.nodes[neighbor]['lon'], G.nodes[neighbor]['lat'], end_lng, end_lat)
                new_path = path + [neighbor]
                heapq.heappush(open_set, (f_score[neighbor], neighbor, new_path))

    logging.warning(f"Không tìm thấy đường từ {source} đến {target}")
    return []

def find_route(start_lat, start_lng, end_lat, end_lng, graph,
               db_config={'host': 'localhost', 'user': 'root', 'password': '', 'database': 'map_app'},
               penalty_factors={'slow': 2, 'blocked': 10, 'closed': 1000}):
    try:
        if not test_db_connection(db_config):
            logging.error("Không thể kết nối CSDL")
            return []

        if graph is None:
            logging.error("Đồ thị không được cung cấp")
            return []

        start_node, G = snap_to_edge(graph, start_lat, start_lng)
        if start_node is None:
            logging.error(f"Không snap được điểm bắt đầu tại ({start_lat}, {start_lng})")
            return []

        end_node, G = snap_to_edge(G, end_lat, end_lng)
        if end_node is None:
            logging.error(f"Không snap được điểm kết thúc tại ({end_lat}, {end_lng})")
            return []

        G_modified = apply_traffic_penalties(G, db_config, penalty_factors)

        if not nx.has_path(G_modified.to_undirected(), start_node, end_node):
            logging.error(f"Không có đường nối giữa node {start_node} và {end_node}")
            return []

        path = a_star_path(G_modified, start_node, end_node, end_lat, end_lng)
        if not path:
            logging.error("Không tìm thấy đường đi")
            return []

        # Tạo route với start và end tọa độ gốc
        route = [[start_lat, start_lng]] + \
                [[G_modified.nodes[node]['lat'], G_modified.nodes[node]['lon']] for node in path] + \
                [[end_lat, end_lng]]
        logging.info(f"Tìm thấy đường đi với {len(route)} điểm, bắt đầu tại ({start_lat}, {start_lng}), kết thúc tại ({end_lat}, {end_lng})")
        return route

    except Exception as e:
        logging.error(f"Lỗi tìm đường: {str(e)}")
        return []