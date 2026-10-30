import json
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError
from face_mapping.config import PipelineConfig, PreprocessConfig, load_config
from face_mapping.coordinates import resize_labels, resize_mask, transform_points
from face_mapping.preprocessing import load_image


def test_coordinate_roundtrip_anisotropic():
    points = [[.5,.5],[500.5,200.5],[1001,499]]
    work = transform_points(points,.401,.399)
    assert np.allclose(transform_points(work,.401,.399,inverse=True),points)


def test_masks_nearest_exact_membership():
    labels = np.array([[0,1],[2,2]],np.int32)
    assert set(np.unique(resize_labels(labels,[9,7])))=={0,1,2}
    assert resize_mask(labels>0,[9,7]).dtype==bool
    assert np.array_equal(resize_labels(labels,[9,7])>0,resize_mask(labels>0,[9,7]))


def test_noninteger_resize_uses_declared_pixel_centers():
    labels = np.array([[1,2,3]],np.int32)
    assert resize_labels(labels,[7,1]).tolist()==[[1,1,2,2,2,3,3]]
    assert resize_mask(labels==1,[7,1]).tolist()==[[True,True,False,False,False,False,False]]


def test_config_strict_units():
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate({"fracture":{"threshold":-1}})
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate({"lithology":{"clustres":3}})
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate({"calibration":{"meters_per_pixel":.001}})
    with pytest.raises(ValidationError):
        PipelineConfig.model_validate({"calibration":{"meters_per_pixel":float('nan'),"planar_approximation_acknowledged":True}})
    configured = PipelineConfig.model_validate({"geometry":{"image_axis_azimuth_deg":30,"attitude_min_trace_length_px":12}})
    assert configured.geometry.image_axis_azimuth_deg == 30
    assert load_config("configs/default.yaml")==PipelineConfig()


def test_exif_and_resize(tmp_path):
    # Orientation=6 maps source 100x60 to oriented 60x100 (90deg clockwise).
    array = np.full((60,100,3),120,np.uint8)
    array[:, :30] = [230,40,20]
    path = tmp_path/"方向.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.fromarray(array).save(path,exif=exif)
    original,working,meta,_ = load_image(path,PreprocessConfig(max_dimension=50,enhance=False))
    assert original.shape[:2]==(100,60)
    assert working.shape[:2]==(50,30)
    assert meta["exif_transposed"]
    assert meta["source_size"]==[100,60]
    assert meta["original_size"]==[60,100]
    assert meta["scale_x"]==meta["scale_y"]==.5
    assert original[5,30,0]>original[85,30,0]


@pytest.mark.parametrize("value",[0,255,127])
def test_constant_rejected(tmp_path,value):
    path=tmp_path/"flat.png"
    Image.fromarray(np.full((32,32,3),value,np.uint8)).save(path)
    with pytest.raises(ValueError):
        load_image(path,PreprocessConfig())


def test_corrupt_rejected(tmp_path):
    path=tmp_path/"broken.png"
    path.write_bytes(b"not an image")
    with pytest.raises((ValueError,OSError)):
        load_image(path,PreprocessConfig())
