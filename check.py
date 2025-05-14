import networkx as nx
import xml.etree.ElementTree as ET
import folium
import logging
import json
from collections import defaultdict

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_graphml(graphml_file):
    """Tải đồ thị từ file GraphML."""
    logging.info(f"Tải đồ thị từ {graphml_file}")
    G = nx.read_graphml(graphml_file, node_type=int)
    for node, data in G.nodes(data=True):
        data['lat'] = float(data['lat'])
        data['lon'] = float(data['lon'])
    logging.info(f"Đồ thị GraphML: {G.number_of_nodes()} node, {G.number_of_edges()} cạnh")
    return G

def parse_osm_file(osm_file):
    """Đọc file OSM, trích xuất node và way, chỉ lấy các highway dành cho xe."""
    logging.info(f"Đọc file OSM: {osm_file}")
    tree = ET.parse(osm_file)
    root = tree.getroot()

    nodes = {}
    ways = []
    
    # Đọc node
    for node in root.findall('node'):
        node_id = int(node.get('id'))
        lat = float(node.get('lat'))
        lon = float(node.get('lon'))
        nodes[node_id] = {'lat': lat, 'lon': lon}
    
    # Đọc way, chỉ lấy các highway dành cho xe
    vehicle_highways = [
        'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
        'unclassified', 'residential', 'service', 'track',
        'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link'
    ]
    for way in root.findall('way'):
        way_id = int(way.get('id'))
        way_nodes = [int(nd.get('ref')) for nd in way.findall('nd')]
        tags = {tag.get('k'): tag.get('v') for tag in way.findall('tag')}
        if 'highway' in tags and tags['highway'] in vehicle_highways:
            ways.append({'id': way_id, 'nodes': way_nodes, 'tags': tags})
            logging.info(f"Thêm way {way_id} với highway={tags['highway']}")
        else:
            logging.info(f"Bỏ qua way {way_id} vì không phải đường xe: highway={tags.get('highway')}")

    logging.info(f"OSM file: {len(nodes)} node, {len(ways)} way (chỉ đường xe)")
    return nodes, ways

def compare_nodes(graph_nodes, osm_nodes):
    """So sánh node giữa đồ thị GraphML và file OSM."""
    logging.info("So sánh node...")
    
    # Trích xuất node ID từ NodeDataView
    graph_node_ids = set(node for node, data in graph_nodes)
    osm_node_ids = set(osm_nodes.keys())
    
    missing_in_graph = osm_node_ids - graph_node_ids
    extra_in_graph = graph_node_ids - osm_node_ids
    
    coord_mismatches = []
    for node_id, data in graph_nodes:
        if node_id in osm_nodes:
            g_lat, g_lon = data['lat'], data['lon']
            o_lat, o_lon = osm_nodes[node_id]['lat'], osm_nodes[node_id]['lon']
            if abs(g_lat - o_lat) > 1e-6 or abs(g_lon - o_lon) > 1e-6:
                coord_mismatches.append((node_id, (g_lat, g_lon), (o_lat, o_lon)))
    
    print(f"Node trong GraphML: {len(graph_node_ids)}")
    print(f"Node trong OSM (chỉ đường xe): {len(osm_node_ids)}")
    print(f"Node thiếu trong GraphML: {len(missing_in_graph)}")
    print(f"Node thừa trong GraphML: {len(extra_in_graph)}")
    print(f"Node có tọa độ không khớp: {len(coord_mismatches)}")
    if coord_mismatches:
        print("Ví dụ tọa độ không khớp:", coord_mismatches[:5])
    
    return len(missing_in_graph) == 0 and len(extra_in_graph) == 0 and len(coord_mismatches) == 0

def compare_ways(G, osm_ways):
    """So sánh way/cạnh giữa đồ thị GraphML và file OSM."""
    logging.info("So sánh way/cạnh...")
    
    # Tạo danh sách cạnh từ OSM ways
    osm_edges = []
    osm_way_tags = {}
    for way in osm_ways:
        way_id = way['id']
        nodes = way['nodes']
        tags = way['tags']
        is_oneway = tags.get('oneway', 'no') == 'yes'
        for i in range(len(nodes) - 1):
            n1, n2 = nodes[i], nodes[i + 1]
            osm_edges.append((n1, n2))
            if not is_oneway:
                osm_edges.append((n2, n1))
        osm_way_tags[way_id] = tags
    
    # Tạo danh sách cạnh từ GraphML
    graph_edges_set = set((u, v) for u, v in G.edges())
    osm_edges_set = set(osm_edges)
    
    missing_edges = osm_edges_set - graph_edges_set
    extra_edges = graph_edges_set - osm_edges_set
    
    # So sánh tag highway
    tag_mismatches = []
    for way_id, tags in osm_way_tags.items():
        highway_osm = tags.get('highway')
        # Tìm cạnh trong GraphML tương ứng với way
        for u, v, data in G.edges(data=True):
            tags_graph = json.loads(data['tags'])
            if tags_graph.get('highway') == highway_osm:
                break
        else:
            tag_mismatches.append((way_id, highway_osm))
    
    print(f"Cạnh trong GraphML: {len(graph_edges_set)}")
    print(f"Cạnh trong OSM (chỉ đường xe): {len(osm_edges_set)}")
    print(f"Cạnh thiếu trong GraphML: {len(missing_edges)}")
    print(f"Cạnh thừa trong GraphML: {len(extra_edges)}")
    print(f"Way có tag highway không khớp: {len(tag_mismatches)}")
    if tag_mismatches:
        print("Ví dụ tag không khớp:", tag_mismatches[:5])
    
    return len(missing_edges) == 0 and len(extra_edges) == 0 and len(tag_mismatches) == 0

def visualize_comparison(graph_nodes, graph_edges, osm_nodes, osm_ways, output_html='comparison_map.html'):
    """Vẽ đồ thị GraphML và OSM lên bản đồ folium để so sánh trực quan."""
    logging.info("Tạo bản đồ so sánh...")
    
    # Tạo bản đồ folium
    center_lat = (21.00017 + 21.01114) / 2
    center_lon = (105.82875 + 105.83827) / 2
    m = folium.Map(location=[center_lat, center_lon], zoom_start=15, tiles='OpenStreetMap')
    
    # Vẽ node từ GraphML (màu xanh)
    for node_id, data in graph_nodes:
        folium.CircleMarker(
            location=[data['lat'], data['lon']],
            radius=3,
            color='blue',
            fill=True,
            fill_opacity=0.5,
            popup=f"GraphML Node {node_id}"
        ).add_to(m)
    
    # Vẽ cạnh từ GraphML (màu xanh)
    for u, v, data in graph_edges.edges(data=True):
        u_data = graph_edges.nodes[u]
        v_data = graph_edges.nodes[v]
        u_lat, u_lon = u_data['lat'], u_data['lon']
        v_lat, v_lon = v_data['lat'], v_data['lon']
        folium.PolyLine(
            locations=[[u_lat, u_lon], [v_lat, v_lon]],
            color='blue',
            weight=3,
            opacity=0.5,
            popup=f"GraphML Edge ({u}, {v})"
        ).add_to(m)
    
    # Vẽ node từ OSM (chỉ đường xe) (màu đỏ)
    for node_id, data in osm_nodes.items():
        # Chỉ vẽ node được sử dụng trong ways của đường xe
        used_nodes = set()
        for way in osm_ways:
            used_nodes.update(way['nodes'])
        if node_id in used_nodes:
            folium.CircleMarker(
                location=[data['lat'], data['lon']],
                radius=3,
                color='red',
                fill=True,
                fill_opacity=0.5,
                popup=f"OSM Node {node_id}"
            ).add_to(m)
    
    # Vẽ way từ OSM (chỉ đường xe) (màu đỏ)
    for way in osm_ways:
        coords = [[osm_nodes[n]['lat'], osm_nodes[n]['lon']] for n in way['nodes'] if n in osm_nodes]
        if len(coords) > 1:
            folium.PolyLine(
                locations=coords,
                color='red',
                weight=3,
                opacity=0.5,
                popup=f"OSM Way {way['id']}"
            ).add_to(m)
    
    # Lưu bản đồ
    m.save(output_html)
    logging.info(f"Đã lưu bản đồ so sánh vào {output_html}")

def main(osm_file='kim_lien.osm', graphml_file='road_network.graphml'):
    # Tải dữ liệu
    G = load_graphml(graphml_file)
    osm_nodes, osm_ways = parse_osm_file(osm_file)
    
    # So sánh node
    nodes_match = compare_nodes(G.nodes(data=True), osm_nodes)
    
    # So sánh way/cạnh
    edges_match = compare_ways(G, osm_ways)
    
    # Trực quan hóa
    visualize_comparison(G.nodes(data=True), G, osm_nodes, osm_ways)
    
    # Kết luận
    if nodes_match and edges_match:
        print("Đồ thị GraphML khớp hoàn toàn với dữ liệu OSM (chỉ đường xe)!")
    else:
        print("Có sự khác biệt giữa đồ thị GraphML và dữ liệu OSM (chỉ đường xe). Kiểm tra log và bản đồ so sánh.")

if __name__ == "__main__":
    main()