import sys
import folium
import mysql.connector
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QMessageBox
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import QUrl, Qt, QObject, pyqtSlot
from PyQt5.QtWebChannel import QWebChannel
from login import Ui_MainWindow as Ui_LoginMainWindow
from admin_interface import Ui_MainWindow as Ui_AdminMainWindow
from user_interface import Ui_MainWindow as Ui_UserMainWindow
import xml.etree.ElementTree as ET
import os
import json
import logging
from overpass_utils import find_nearest_way
from routing import find_route

# Thiết lập logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

class Bridge(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent

    @pyqtSlot(float, float)
    def markerClicked(self, lat, lng):
        if hasattr(self.parent, 'markerClicked'):
            self.parent.markerClicked(lat, lng)
        else:
            logging.warning(f"markerClicked not implemented in {type(self.parent).__name__}")

    @pyqtSlot(str)
    def waySelected(self, way_id):
        if hasattr(self.parent, 'waySelected'):
            self.parent.waySelected(way_id)
        else:
            logging.warning(f"waySelected not implemented in {type(self.parent).__name__}")

    @pyqtSlot(float, float)
    def findNearestWay(self, lat, lng):
        if hasattr(self.parent, 'find_nearest_way'):
            self.parent.find_nearest_way(lat, lng)
        else:
            logging.warning(f"findNearestWay not implemented in {type(self.parent).__name__}")

class LoginMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_LoginMainWindow()
        self.ui.setupUi(self)

        self.ui.loginButton.clicked.connect(self.check_login)

        try:
            self.db = mysql.connector.connect(
                host="localhost",
                user="root",
                password="",
                database="map_app"
            )
            self.cursor = self.db.cursor(dictionary=True)
            logging.info("Kết nối CSDL thành công")
        except mysql.connector.Error as err:
            logging.error(f"Lỗi kết nối CSDL: {err}")
            QMessageBox.critical(self, "Database Error", f"Failed to connect to database: {err}")
            sys.exit(1)

    def check_login(self):
        username = self.ui.usernameEdit.text()
        password = self.ui.passwordEdit.text()

        query = "SELECT username, password, role FROM users WHERE username = %s AND password = %s"
        try:
            self.cursor.execute(query, (username, password))
            user = self.cursor.fetchone()
        except mysql.connector.Error as err:
            logging.error(f"Lỗi truy vấn đăng nhập: {err}")
            QMessageBox.critical(self, "Lỗi", f"Không thể truy vấn CSDL: {err}")
            return

        if user:
            self.role = user["role"]
            self.close()
            if self.role == "admin":
                self.open_admin_interface()
            else:
                self.open_user_interface()
        else:
            QMessageBox.warning(self, "Error", "Invalid username or password")

    def open_admin_interface(self):
        self.admin_window = AdminMainWindow(self.db, self.cursor)
        self.admin_window.ui.logoutButton.clicked.connect(self.logout_admin)
        self.admin_window.show()

    def open_user_interface(self):
        self.user_window = UserMainWindow(self.db, self.cursor)
        self.user_window.ui.logoutButton.clicked.connect(self.logout_user)
        self.user_window.show()

    def logout_admin(self):
        self.admin_window.close()
        self.__init__()
        self.show()

    def logout_user(self):
        self.user_window.close()
        self.__init__()
        self.show()

    def closeEvent(self, event):
        if hasattr(self, 'cursor'):
            self.cursor.close()
        if hasattr(self, 'db') and self.db.is_connected():
            self.db.close()
            logging.info("Đóng kết nối CSDL")
        event.accept()

class AdminMainWindow(QMainWindow):
    def __init__(self, db, cursor):
        super().__init__()
        self.ui = Ui_AdminMainWindow()
        self.ui.setupUi(self)

        self.db = db
        self.cursor = cursor
        self.db_config = {
            'host': 'localhost',
            'user': 'root',
            'password': '',
            'database': 'map_app'
        }

        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(800, 600)

        self.web_view = QWebEngineView(self.ui.mapWidget)
        layout = QVBoxLayout(self.ui.mapWidget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web_view)

        self.editing_traffic = False
        self.selected_way_id = None
        self.selected_coords = None
        self.highlighted_ways = set()

        self.channel = QWebChannel()
        self.bridge = Bridge(self)
        self.channel.registerObject('pyObj', self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        self.create_initial_map()
        self.ui.editTrafficButton.clicked.connect(self.toggle_traffic_editing)
        self.ui.deleteButton.clicked.connect(self.delete_traffic_editing)
        logging.info("Khởi tạo AdminMainWindow thành công")

    def create_initial_map(self):
        try:
            min_lat = 21.0001700
            min_lon = 105.8287500
            max_lat = 21.0111400
            max_lon = 105.8382700

            html_template = f"""
            <!DOCTYPE html>
            <html>
                <head>
                    <meta charset="utf-8">
                    <title>Bản đồ cố định</title>
                    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
                    <style>
                        #map {{
                            position: absolute;
                            top: 0;
                            bottom: 0;
                            left: 0;
                            right: 0;
                            background-color: #e8e8e8;
                        }}
                        body {{
                            margin: 0;
                            padding: 0;
                            overflow: hidden;
                        }}
                        .leaflet-control-container {{
                            display: none !important;
                        }}
                    </style>
                </head>
                <body>
                    <div id="map"></div>
                    
                    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
                    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
                    <script>
                        var map = L.map('map', {{
                            zoomControl: false,
                            scrollWheelZoom: false,
                            doubleClickZoom: false,
                            touchZoom: false,
                            boxZoom: false,
                            keyboard: false,
                            dragging: true,
                            zoomSnap: 0,
                            zoomDelta: 0
                        }}).setView([{(min_lat + max_lat)/2}, {(min_lon + max_lon)/2}], 18);
                        
                        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                            attribution: '© OpenStreetMap',
                            noWrap: true,
                            bounds: [[{min_lat}, {min_lon}], [{max_lat}, {max_lon}]],
                            minZoom: 18,
                            maxZoom: 18
                        }}).addTo(map);
                        
                        var currentMapClick = null;
                        var highlightedWay = null;
                        var highlightedWays = {{}};
                        
                        new QWebChannel(qt.webChannelTransport, function(channel) {{
                            window.pyObj = channel.objects.pyObj;
                        }});
                        
                        map.setZoom(18);
                        map.options.minZoom = 18;
                        map.options.maxZoom = 18;

                        if (!window.highlightedWays) {{
                            window.highlightedWays = {{}};
                        }}
                    </script>
                </body>
            </html>
            """

            map_file = os.path.join(os.path.dirname(__file__), "admin_map_temp.html")
            with open(map_file, 'w', encoding='utf-8') as f:
                f.write(html_template)

            self.web_view.loadFinished.connect(self.on_map_loaded)
            self.web_view.load(QUrl.fromLocalFile(os.path.abspath(map_file)))
            logging.info("Tạo bản đồ và chờ loadFinished")

        except Exception as e:
            logging.error(f"Lỗi tạo bản đồ: {e}")
            QMessageBox.critical(self, "Lỗi", f"Không thể tạo bản đồ: {str(e)}")

    def on_map_loaded(self, ok):
        if ok:
            logging.info("Bản đồ đã tải xong, gọi highlight_traffic_changes")
            self.highlight_traffic_changes()
        else:
            logging.error("Lỗi tải bản đồ")
            QMessageBox.critical(self, "Lỗi", "Không thể tải bản đồ")

    def highlight_traffic_changes(self):
        try:
            if not self.db.is_connected():
                logging.warning("CSDL không kết nối, thử kết nối lại trong highlight_traffic_changes")
                self.db = mysql.connector.connect(**self.db_config)
                self.cursor = self.db.cursor(dictionary=True)
                logging.info("Kết nối lại CSDL thành công trong highlight_traffic_changes")

            query = "SELECT DISTINCT way_id, coordinates FROM traffic_changes WHERE coordinates IS NOT NULL"
            self.cursor.execute(query)
            ways = self.cursor.fetchall()
            logging.info(f"Lấy được {len(ways)} way_id từ traffic_changes: {[w['way_id'] for w in ways]}")

            failed_ways = []
            for way in ways:
                way_id = way['way_id']
                if way_id not in self.highlighted_ways:
                    try:
                        coords = json.loads(way['coordinates'])
                        if isinstance(coords, list) and all(isinstance(c, list) and len(c) == 2 for c in coords):
                            coords_json = json.dumps(coords)
                            self.web_view.page().runJavaScript(f"""
                                if (!window.highlightedWays) {{
                                    window.highlightedWays = {{}};
                                }}
                                if (window.highlightedWays['{way_id}']) {{
                                    map.removeLayer(window.highlightedWays['{way_id}']);
                                }}
                                window.highlightedWays['{way_id}'] = L.polyline({coords_json}, {{
                                    color: 'red',
                                    weight: 5,
                                    opacity: 0.8
                                }}).addTo(map);
                            """)
                            self.highlighted_ways.add(way_id)
                            logging.info(f"Highlighted way_id={way_id} with {len(coords)} coordinates")
                        else:
                            logging.warning(f"Tọa độ không hợp lệ cho way_id={way_id}")
                            failed_ways.append(way_id)
                    except json.JSONDecodeError as e:
                        logging.error(f"Lỗi giải mã JSON cho way_id={way_id}: {e}")
                        failed_ways.append(way_id)

            if failed_ways:
                QMessageBox.warning(
                    self,
                    "Cảnh báo",
                    f"Không thể highlight các đoạn đường sau do tọa độ không hợp lệ: {', '.join(failed_ways)}"
                )

        except mysql.connector.Error as err:
            logging.error(f"Lỗi truy vấn CSDL trong highlight_traffic_changes: {err}")
            QMessageBox.critical(self, "Lỗi", f"Không thể truy vấn CSDL: {err}")
        except Exception as e:
            logging.error(f"Lỗi không xác định trong highlight_traffic_changes: {e}")
            QMessageBox.critical(self, "Lỗi", f"Lỗi không xác định: {str(e)}")

    def find_nearest_way(self, marker_lat, marker_lon):
        try:
            way_id, way_nodes = find_nearest_way(marker_lat, marker_lon)
            if way_id and way_nodes:
                self.selected_way_id = way_id
                self.selected_coords = way_nodes
                coords_json = json.dumps(way_nodes)
                self.web_view.page().runJavaScript(f"""
                    if (window.highlightedWay) {{
                        map.removeLayer(window.highlightedWay);
                    }}
                    window.highlightedWay = L.polyline({coords_json}, {{
                        color: 'red',
                        weight: 5,
                        opacity: 0.8
                    }}).addTo(map);
                    try {{
                        console.log('Selected way_id:', '{way_id}');
                        window.pyObj.waySelected('{way_id}');
                    }} catch (e) {{
                        console.error('Error calling waySelected:', e);
                    }}
                """)
                logging.info(f"Highlighted way_id={way_id} with {len(way_nodes)} coordinates")
                return way_id
            else:
                logging.warning("Không tìm thấy đoạn đường trong khu vực hoặc không có tọa độ")
                QMessageBox.warning(self, "Cảnh báo", "Không tìm thấy đoạn đường trong khu vực.")
                self.selected_way_id = None
                self.selected_coords = None
                return None
        except Exception as e:
            logging.error(f"Lỗi tìm đoạn đường: {e}")
            QMessageBox.critical(self, "Lỗi", f"Không thể tìm đoạn đường: {str(e)}")
            self.selected_way_id = None
            self.selected_coords = None
            return None

    def toggle_traffic_editing(self):
        logging.info(f"toggle_traffic_editing: editing_traffic={self.editing_traffic}")
        if not self.editing_traffic:
            self.editing_traffic = True
            self.ui.editTrafficButton.setText("Lưu")
            self.web_view.page().runJavaScript("""
                document.getElementById('map').style.cursor = 'pointer';
                if (window.currentMapClick) {
                    map.off('click', window.currentMapClick);
                }
                window.currentMapClick = function(e) {
                    window.pyObj.findNearestWay(e.latlng.lat, e.latlng.lng);
                };
                map.on('click', window.currentMapClick);
            """)
            logging.info("Bắt đầu chỉnh sửa giao thông")
        else:
            self.editing_traffic = False
            self.ui.editTrafficButton.setText("Chỉnh sửa giao thông")
            logging.info("Gọi save_traffic_changes")
            self.save_traffic_changes()
            self.web_view.page().runJavaScript("""
                document.getElementById('map').style.cursor = '';
                if (window.currentMapClick) {
                    map.off('click', window.currentMapClick);
                    window.currentMapClick = null;
                }
            """)
            logging.info("Kết thúc chỉnh sửa giao thông")

    def waySelected(self, way_id):
        try:
            if way_id and isinstance(way_id, str):
                self.selected_way_id = way_id
                logging.info(f"waySelected: Way ID={way_id}")
                self.statusBar().showMessage(f"Way ID: {way_id}")
            else:
                logging.warning("waySelected: Không có way_id hợp lệ")
                self.statusBar().showMessage("Không có đường được chọn")
                self.selected_coords = None
        except Exception as e:
            logging.error(f"Lỗi trong waySelected: {e}")
            self.statusBar().showMessage("Lỗi khi chọn đường")
            QMessageBox.warning(self, "Cảnh báo", f"Lỗi khi xử lý way_id: {str(e)}")
            self.selected_coords = None

    def save_traffic_changes(self):
        logging.info("Bắt đầu save_traffic_changes")
        if self.selected_way_id and self.selected_coords:
            try:
                if not self.db.is_connected():
                    logging.warning("CSDL không kết nối, thử kết nối lại")
                    self.db = mysql.connector.connect(**self.db_config)
                    self.cursor = self.db.cursor(dictionary=True)
                    logging.info("Kết nối lại CSDL thành công")
                
                coordinates_json = json.dumps(self.selected_coords)
                logging.info(f"Thực thi INSERT: way_id={self.selected_way_id}, status=updated, coordinates={coordinates_json[:50]}...")
                query = "INSERT INTO traffic_changes (way_id, status, coordinates) VALUES (%s, %s, %s)"
                self.cursor.execute(query, (
                    self.selected_way_id,
                    'updated',
                    coordinates_json
                ))
                self.db.commit()
                logging.info(f"Đã lưu way_id={self.selected_way_id} với {len(self.selected_coords)} tọa độ vào CSDL")

                if self.selected_way_id not in self.highlighted_ways:
                    coords_json = json.dumps(self.selected_coords)
                    self.web_view.page().runJavaScript(f"""
                        if (!window.highlightedWays) {{
                            window.highlightedWays = {{}};
                        }}
                        if (window.highlightedWays['{self.selected_way_id}']) {{
                            map.removeLayer(window.highlightedWays['{self.selected_way_id}']);
                        }}
                        window.highlightedWays['{self.selected_way_id}'] = L.polyline({coords_json}, {{
                            color: 'red',
                            weight: 5,
                            opacity: 0.8
                        }}).addTo(map);
                    """)
                    self.highlighted_ways.add(self.selected_way_id)
                    logging.info(f"Highlighted new way_id={self.selected_way_id} với {len(self.selected_coords)} coordinates")

                QMessageBox.information(self, "Thông báo", "Đã lưu thay đổi giao thông!")
            except mysql.connector.Error as err:
                logging.error(f"Lỗi lưu CSDL: {err}")
                QMessageBox.critical(self, "Lỗi", f"Không thể lưu vào cơ sở dữ liệu: {err}")
            except Exception as e:
                logging.error(f"Lỗi không xác định trong save_traffic_changes: {e}")
                QMessageBox.critical(self, "Lỗi", f"Lỗi không xác định: {str(e)}")
        else:
            logging.warning("save_traffic_changes: Chưa chọn đoạn đường hoặc không có tọa độ")
            QMessageBox.warning(self, "Cảnh báo", "Chưa chọn đoạn đường hoặc không có tọa độ để lưu!")

    def closeEvent(self, event):
        self.web_view.page().runJavaScript("""
            if (window.currentMapClick) {
                map.off('click', window.currentMapClick);
            }
            if (window.highlightedWay) {
                map.removeLayer(window.highlightedWay);
            }
            for (var way_id in window.highlightedWays) {
                if (window.highlightedWays[way_id]) {
                    map.removeLayer(window.highlightedWays[way_id]);
                }
            }
        """)
        if hasattr(self, 'temp_file') and os.path.exists(self.temp_file):
            os.remove(self.temp_file)
        if self.db.is_connected():
            self.cursor.close()
            self.db.close()
            logging.info("Đóng kết nối CSDL trong AdminMainWindow")
        event.accept()
    
    def delete_traffic_editing(self):
        print("Chưa triển khai")

class UserMainWindow(QMainWindow):
    def __init__(self, db, cursor):
        super().__init__()
        self.ui = Ui_UserMainWindow()
        self.ui.setupUi(self)

        self.db = db
        self.cursor = cursor
        self.db_config = {
            'host': 'localhost',
            'user': 'root',
            'password': '',
            'database': 'map_app'
        }

        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setMinimumSize(800, 600)

        self.web_view = QWebEngineView(self.ui.mapWidget)
        layout = QVBoxLayout(self.ui.mapWidget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web_view)

        self.markers = []
        self.current_marker_index = 0
        self.highlighted_ways = set()
        self.start = None
        self.end = None

        self.channel = QWebChannel()
        self.bridge = Bridge(self)
        self.channel.registerObject('pyObj', self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        self.create_initial_map()

        self.ui.directionButton.clicked.connect(self.find_direction)
        self.ui.markButton.clicked.connect(self.add_marker)
        logging.info("Khởi tạo UserMainWindow thành công")

    def create_initial_map(self):
        try:
            min_lat = 21.0001700
            min_lon = 105.8287500
            max_lat = 21.0111400
            max_lon = 105.8382700

            html_template = f"""
            <!DOCTYPE html>
            <html>
                <head>
                    <meta charset="utf-8">
                    <title>Bản đồ cố định</title>
                    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
                    <style>
                        #map {{
                            position: absolute;
                            top: 0;
                            bottom: 0;
                            left: 0;
                            right: 0;
                            background-color: #e8e8e8;
                        }}
                        body {{
                            margin: 0;
                            padding: 0;
                            overflow: hidden;
                        }}
                        .leaflet-control-container {{
                            display: none !important;
                        }}
                    </style>
                </head>
                <body>
                    <div id="map"></div>
                    
                    <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
                    <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
                    <script>
                        var map = L.map('map', {{
                            zoomControl: false,
                            scrollWheelZoom: false,
                            doubleClickZoom: false,
                            touchZoom: false,
                            boxZoom: false,
                            keyboard: false,
                            dragging: true,
                            zoomSnap: 0,
                            zoomDelta: 0
                        }}).setView([{(min_lat + max_lat)/2}, {(min_lon + max_lon)/2}], 18);
                        
                        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                            attribution: '© OpenStreetMap',
                            noWrap: true,
                            bounds: [[{min_lat}, {min_lon}], [{max_lat}, {max_lon}]],
                            minZoom: 18,
                            maxZoom: 18
                        }}).addTo(map);
                        
                        window.markers = [];
                        window.currentMapClick = null;
                        window.directionLine = null;
                        var highlightedWays = {{}};
                        
                        new QWebChannel(qt.webChannelTransport, function(channel) {{
                            window.pyObj = channel.objects.pyObj;
                        }});
                        
                        map.setZoom(18);
                        map.options.minZoom = 18;
                        map.options.maxZoom = 18;

                        if (!window.highlightedWays) {{
                            window.highlightedWays = {{}};
                        }}
                    </script>
                </body>
            </html>
            """

            map_file = os.path.join(os.path.dirname(__file__), "user_map_temp.html")
            with open(map_file, 'w', encoding='utf-8') as f:
                f.write(html_template)

            self.web_view.loadFinished.connect(self.on_map_loaded)
            self.web_view.load(QUrl.fromLocalFile(os.path.abspath(map_file)))
            logging.info("Tạo bản đồ UserMainWindow và chờ loadFinished")

        except Exception as e:
            logging.error(f"Lỗi tạo bản đồ: {e}")
            QMessageBox.critical(self, "Lỗi", f"Không thể tạo bản đồ: {str(e)}")

    def on_map_loaded(self, ok):
        if ok:
            logging.info("Bản đồ UserMainWindow đã tải xong, gọi highlight_traffic_changes")
            self.highlight_traffic_changes()
        else:
            logging.error("Lỗi tải bản đồ UserMainWindow")
            QMessageBox.critical(self, "Lỗi", "Không thể tải bản đồ")

    def highlight_traffic_changes(self):
        try:
            if not self.db.is_connected():
                logging.warning("CSDL không kết nối, thử kết nối lại trong highlight_traffic_changes (UserMainWindow)")
                self.db = mysql.connector.connect(**self.db_config)
                self.cursor = self.db.cursor(dictionary=True)
                logging.info("Kết nối lại CSDL thành công trong highlight_traffic_changes (UserMainWindow)")

            query = "SELECT DISTINCT way_id, coordinates FROM traffic_changes WHERE coordinates IS NOT NULL"
            self.cursor.execute(query)
            ways = self.cursor.fetchall()
            logging.info(f"Lấy được {len(ways)} way_id từ traffic_changes (UserMainWindow): {[w['way_id'] for w in ways]}")

            failed_ways = []
            for way in ways:
                way_id = way['way_id']
                if way_id not in self.highlighted_ways:
                    try:
                        coords = json.loads(way['coordinates'])
                        if isinstance(coords, list) and all(isinstance(c, list) and len(c) == 2 for c in coords):
                            coords_json = json.dumps(coords)
                            self.web_view.page().runJavaScript(f"""
                                if (!window.highlightedWays) {{
                                    window.highlightedWays = {{}};
                                }}
                                if (window.highlightedWays['{way_id}']) {{
                                    map.removeLayer(window.highlightedWays['{way_id}']);
                                }}
                                window.highlightedWays['{way_id}'] = L.polyline({coords_json}, {{
                                    color: 'red',
                                    weight: 5,
                                    opacity: 0.8
                                }}).addTo(map);
                            """)
                            self.highlighted_ways.add(way_id)
                            logging.info(f"Highlighted way_id={way_id} with {len(coords)} coordinates (UserMainWindow)")
                        else:
                            logging.warning(f"Tọa độ không hợp lệ cho way_id={way_id} (UserMainWindow)")
                            failed_ways.append(way_id)
                    except json.JSONDecodeError as e:
                        logging.error(f"Lỗi giải mã JSON cho way_id={way_id} (UserMainWindow): {e}")
                        failed_ways.append(way_id)

            if failed_ways:
                QMessageBox.warning(
                    self,
                    "Cảnh báo",
                    f"Không thể highlight các đoạn đường sau do tọa độ không hợp lệ: {', '.join(failed_ways)}"
                )

        except mysql.connector.Error as err:
            logging.error(f"Lỗi truy vấn CSDL trong highlight_traffic_changes (UserMainWindow): {err}")
            QMessageBox.critical(self, "Lỗi", f"Không thể truy vấn CSDL: {err}")
        except Exception as e:
            logging.error(f"Lỗi không xác định trong highlight_traffic_changes (UserMainWindow): {e}")
            QMessageBox.critical(self, "Lỗi", f"Lỗi không xác định: {str(e)}")

    def add_marker(self):
        self.web_view.page().runJavaScript("""
            document.getElementById('map').style.cursor = 'crosshair';
            
            if (window.currentMapClick) {
                map.off('click', window.currentMapClick);
            }
            
            window.currentMapClick = function(e) {
                if (window.pyObj) {
                    window.pyObj.markerClicked(e.latlng.lat, e.latlng.lng);
                }
            };
            
            map.on('click', window.currentMapClick);
        """)

    def markerClicked(self, lat, lng):
        logging.info(f"Đánh dấu tại: {lat}, {lng}")
        
        if self.current_marker_index == 0:
            self.start = [lat, lng]
            logging.info(f"Lưu tọa độ marker 1 (start): {self.start}")
        else:
            self.end = [lat, lng]
            logging.info(f"Lưu tọa độ marker 2 (end): {self.end}")

        marker_js = f"""
            var newMarker = L.marker([{lat}, {lng}], {{
                icon: L.icon({{
                    iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
                    iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
                    shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
                    iconSize: [25, 41],
                    iconAnchor: [12, 41],
                    popupAnchor: [1, -34]
                }})
            }}).addTo(map);
            
            newMarker.bindPopup("Điểm {self.current_marker_index + 1}");
            
            if (window.markers[{self.current_marker_index}]) {{
                map.removeLayer(window.markers[{self.current_marker_index}]);
            }}
            
            window.markers[{self.current_marker_index}] = newMarker;
        """
        
        self.web_view.page().runJavaScript(marker_js)
        
        self.current_marker_index = 1 - self.current_marker_index
        
        self.web_view.page().runJavaScript("""
            document.getElementById('map').style.cursor = '';
        """)

    def find_direction(self):
        if self.start is None or self.end is None:
            QMessageBox.warning(self, "Cảnh báo", "Cần đánh dấu 2 điểm trên bản đồ!")
            return

        start_lat, start_lng = self.start
        end_lat, end_lng = self.end

        route = find_route(start_lat, start_lng, end_lat, end_lng)

        if route:
            route_json = json.dumps(route)
            self.web_view.page().runJavaScript(f"""
                if (window.directionLine) {{
                    map.removeLayer(window.directionLine);
                }}
                window.directionLine = L.polyline({route_json}, {{
                    color: '#3388ff',
                    weight: 5,
                    opacity: 0.7
                }}).addTo(map);
                
                var bounds = L.latLngBounds({route_json});
                map.fitBounds(bounds, {{ maxZoom: 18 }});
            """)
            logging.info(f"Đã vẽ đường đi với {len(route)} điểm")
        else:
            QMessageBox.warning(self, "Cảnh báo", "Không thể tìm đường đi!")

    def closeEvent(self, event):
        self.web_view.page().runJavaScript("""
            if (window.currentMapClick) {
                map.off('click', window.currentMapClick);
            }
            if (window.directionLine) {
                map.removeLayer(window.directionLine);
            }
            for (var way_id in window.highlightedWays) {
                if (window.highlightedWays[way_id]) {
                    map.removeLayer(window.highlightedWays[way_id]);
                }
            }
            window.markers.forEach(function(marker) {
                if (marker) {
                    map.removeLayer(marker);
                }
            });
        """)
        if hasattr(self, 'temp_file') and os.path.exists(self.temp_file):
            os.remove(self.temp_file)
        if self.db.is_connected():
            self.cursor.close()
            self.db.close()
            logging.info("Đóng kết nối CSDL trong UserMainWindow")
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    login_window = LoginMainWindow()
    login_window.show()
    sys.exit(app.exec_())