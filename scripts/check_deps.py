try:
    import cv2
    print("opencv-python: OK")
except ImportError:
    print("opencv-python: MISSING")

try:
    import pupil_labs.real_time_screen_gaze
    print("pupil-labs-real-time-screen-gaze: OK")
except ImportError:
    print("pupil-labs-real-time-screen-gaze: MISSING")

try:
    import apriltag
    print("apriltag: OK")
except ImportError:
    print("apriltag: MISSING")
