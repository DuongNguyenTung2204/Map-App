import logging
import networkx as nx
import json

# Thiết lập logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def load_graph(graphml_file):
    """
    Tải đồ thị từ file GraphML.
    """
    logging.info(f"Tải đồ thị từ file GraphML: {graphml_file}")
    try:
        G = nx.read_graphml(graphml_file, node_type=int)
        for node, data in G.nodes(data=True):
            data['lat'] = float(data['lat'])
            data['lon'] = float(data['lon'])
        logging.info(f"Đã tải đồ thị với {G.number_of_nodes()} node và {G.number_of_edges()} cạnh")
        return G
    except Exception as e:
        logging.error(f"Lỗi tải đồ thị: {str(e)}")
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

def find_nearest_way(lat, lon, graphml_file='road_network.graphml'):
    """
    Tìm đoạn đường gần nhất với tọa độ (lat, lon) bằng cách duyệt tất cả các cạnh trong đồ thị.
    Trả về way_id và danh sách tọa độ của các node trên đoạn đường đó, sắp xếp theo thứ tự liên tục.
    """
    try:
        G = load_graph(graphml_file)
        min_dist = float('inf')
        nearest_way_id = None
        nearest_way_nodes = []
        nearest_edge = None
        
        # Duyệt qua tất cả các cạnh trong đồ thị
        for u, v, data in G.edges(data=True):
            u_lat, u_lon = G.nodes[u]['lat'], G.nodes[u]['lon']
            v_lat, v_lon = G.nodes[v]['lat'], G.nodes[v]['lon']
            distance = distance_point_to_segment(lat, lon, u_lat, u_lon, v_lat, v_lon)
            if distance < min_dist:
                min_dist = distance
                try:
                    tags = json.loads(data['tags'])
                    nearest_way_id = tags.get('id')
                    if not nearest_way_id:
                        continue
                    nearest_edge = (u, v)
                except (json.JSONDecodeError, KeyError):
                    logging.warning(f"Cạnh ({u}, {v}) có tags không hợp lệ: {data.get('tags')}")
                    continue
        
        if nearest_way_id and nearest_edge:
            # Tìm tất cả các cạnh có cùng way_id để lấy đầy đủ node
            way_nodes = set()
            subgraph_edges = []
            for x, y, edge_data in G.edges(data=True):
                try:
                    edge_tags = json.loads(edge_data['tags'])
                    if edge_tags.get('id') == nearest_way_id:
                        way_nodes.add(x)
                        way_nodes.add(y)
                        subgraph_edges.append((x, y))
                except (json.JSONDecodeError, KeyError):
                    continue
            
            # Tạo đồ thị con từ các cạnh của way
            subgraph = G.edge_subgraph(subgraph_edges).copy()
            
            # Sắp xếp node theo đường đi liên tục
            if way_nodes:
                u, v = nearest_edge
                # Chọn node đầu gần nhất với cạnh gần nhất
                start_node = min(way_nodes, key=lambda n: ((G.nodes[n]['lat'] - G.nodes[u]['lat'])**2 + (G.nodes[n]['lon'] - G.nodes[u]['lon'])**2)**0.5)
                
                # Tìm đường đi bao phủ tất cả node trong way_nodes
                sorted_nodes = [start_node]
                remaining_nodes = way_nodes - {start_node}
                
                while remaining_nodes:
                    # Tìm node tiếp theo trong đồ thị con
                    current_node = sorted_nodes[-1]
                    next_node = None
                    min_path_length = float('inf')
                    
                    for node in remaining_nodes:
                        if nx.has_path(subgraph, current_node, node):
                            path = nx.shortest_path(subgraph, current_node, node)
                            path_length = len(path)
                            if path_length < min_path_length:
                                min_path_length = path_length
                                next_node = node
                                next_path = path
                    
                    if next_node:
                        # Thêm các node trong đường đi (trừ node đầu vì đã có)
                        sorted_nodes.extend(next_path[1:])
                        remaining_nodes.remove(next_node)
                    else:
                        # Nếu không tìm thấy đường đi, thêm node gần nhất theo khoảng cách Euclidean
                        next_node = min(remaining_nodes, key=lambda n: ((G.nodes[n]['lat'] - G.nodes[current_node]['lat'])**2 + (G.nodes[n]['lon'] - G.nodes[current_node]['lon'])**2)**0.5)
                        sorted_nodes.append(next_node)
                        remaining_nodes.remove(next_node)
                
                nearest_way_nodes = [[G.nodes[node]['lat'], G.nodes[node]['lon']] for node in sorted_nodes]
        
        max_distance_m = 50
        if nearest_way_id and min_dist * 111000 < max_distance_m:
            logging.debug(f"Nearest way: ID={nearest_way_id}, Distance={min_dist * 111000:.2f}m, Nodes={len(nearest_way_nodes)}")
            return nearest_way_id, nearest_way_nodes
        else:
            logging.warning(f"Không tìm thấy đường trong khu vực hoặc khoảng cách {min_dist * 111000:.2f}m vượt ngưỡng {max_distance_m}m")
            return None, []
    except Exception as e:
        logging.error(f"Lỗi khi tìm đoạn đường: {e}")
        raise

