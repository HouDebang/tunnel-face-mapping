"""Human labels only: types, defaults and all limits come from PipelineConfig."""
from .config import PipelineConfig
from .stage_graph import STAGES

_FIELDS = {
"preprocessing": {
"max_dimension":("工作图像最大边长","px（工作）","缩小大照片以控制识别耗时；导出仍使用原图坐标。"),
"enhance":("局部亮度增强","","对亮度通道做 CLAHE，减轻光照不均。"),
"clahe_clip":("增强强度","","提高数值会放大局部对比，也可能增强噪声。"),
"clahe_grid":("增强网格数","格","CLAHE 网格分块数。")},
"face":{
"algorithm":("掌子面提取算法","","选择自动候选范围算法；自动边界均未经地质语义验证。"),
"mode":("分析范围模式","","auto 自动提出候选；full_image 明确使用整图。"),
"allow_full_image_fallback":("允许整图回退","","定位不可靠时继续候选分析，并明确标记未验证。"),
"min_area_ratio":("最小区域比例","比例","候选 ROI 占工作图像的最小面积比例。"),
"max_area_ratio":("最大区域比例","比例","候选区域过大时触发回退或报错。"),
"grabcut_iterations":("GrabCut 迭代次数","次","增加迭代次数会延长运行时间。"),
"grabcut_margin":("GrabCut 边缘背景带","比例","按图像宽高设定边缘背景带；图像中心须位于待提取岩体内。")},
"lithology":{
"algorithm":("区域分割算法","","划分视觉候选区域，类别仍为 unknown。"),
"clusters":("初始视觉类别数","个","颜色、纹理和位置聚类数；不代表真实岩性数。"),
"slic_segments":("SLIC 初始超像素数","个","相邻区域合并前的目标分区数量，并非最终岩性数量。"),
"slic_compactness":("SLIC 紧凑度","","增大后更偏向规则分块，减小后更贴近颜色边缘。"),
"min_region_area_ratio":("最小分区面积比例","比例","将过小区域与相邻区域合并。"),
"min_region_pixels":("最小分区像素数","px²（工作）","与面积比例阈值共同控制小区域。"),
"merge_color_distance":("相邻近色区域合并阈值","Lab 距离","提高数值会合并更多相邻近色区域。"),
"spatial_weight":("空间特征权重","","聚类中位置特征相对权重。"),
"max_regions":("最多候选分区数","个","尽量合并相邻区域；孤立 ROI 岛无法强制合并。"),
"random_seed":("随机种子","","固定后可重复比较相同参数。")},
"fracture":{
"algorithm":("裂隙检测算法","","不同方法的响应独立归一化，同一阈值不代表相同检出水平。"),
"clahe_clip":("暗线局部增强强度","","调节检测器内部灰度增强。"),
"blackhat_radius":("暗线增强半径","px（工作）","Blackhat 结构元半径，影响可见线宽。"),
"frangi_sigma_min":("Frangi 最小尺度","px（工作）","多尺度 Hessian 暗线滤波的最小 Gaussian sigma。"),
"frangi_sigma_max":("Frangi 最大尺度","px（工作）","最大尺度须不小于最小尺度；较大尺度检测较宽线状结构。"),
"frangi_scale_count":("Frangi 尺度数量","个","在最小和最大尺度间等距采样，更多尺度会增加耗时。"),
"threshold":("裂缝响应阈值","0–1","与响应分位阈值取较大者；提高后通常减少候选。"),
"min_component_area":("最小连通区域面积","px²（工作）","过滤小噪声斑块。"),
"min_elongation":("最小线状结构比","","PCA 轴比或骨架长度/宽度比的最小要求。"),
"min_line_length":("最小候选连通长度","px（工作）","检测阶段连通目标的最小主轴跨度。"),
"noise_sigma":("降噪平滑强度","px（工作）","Gaussian 标准差；过大会模糊细裂缝。"),
"line_response_percentile":("候选响应分位数","%","与固定阈值联合筛选，增大后候选通常减少。"),
"max_width_ratio":("最大相对宽度","比例","排除较宽的块状目标。")},
"vectorization":{
"min_length_px":("最短保留迹线","px（原图）","骨架按分叉拆段后，过滤过短路径。"),
"simplify_tolerance_px":("显示折线简化容差","px（原图）","只影响显示，长度仍使用完整点列。")},
"calibration":{
"meters_per_pixel":("原图像素尺度","m/px（原图）","有可靠尺度才填写；留空只报告像素单位。"),
"planar_approximation_acknowledged":("确认平面与均匀投影近似","","填写尺度时必须确认；程序不做透视或三维校正。"),
"description":("标定依据说明","","记录尺度来源与适用条件。")},
"geometry":{
"image_axis_azimuth_deg":("图像 X 轴方位角","°","可选的相机/掌子面定向元数据；用于把图像平面方向转换为水平投影，不能单独得到倾角。"),
"attitude_min_trace_length_px":("产状统计最短迹线","px（原图）","过滤过短候选迹线后计算加权轴向方向；0 表示不按长度过滤。")},
}


_CHOICES = {
    "face": {
        "central_texture": {"label": "中心颜色与纹理（原默认）", "description": "用中心 Lab 颜色、局部纹理和连通区域提出候选范围。背景与岩面相近时易混入。"},
        "grabcut": {"label": "GrabCut 图割", "description": "按中心前景、边缘背景的假设进行迭代图割。适合岩面位于中心且与周边有颜色差异的照片；假设不成立时可能截错范围。"},
    },
    "lithology": {
        "kmeans": {"label": "K-means 颜色与纹理（原默认）", "description": "先聚类颜色、纹理及位置特征，再合并相邻小区域。视觉类别不等于真实岩性。"},
        "slic": {"label": "SLIC 超像素与区域合并", "description": "先沿颜色与空间邻近关系生成超像素，再按面积及近色阈值合并。最终区域类别仍为 unknown。"},
    },
    "fracture": {
        "blackhat_hessian": {"label": "Blackhat + Hessian（原默认）", "description": "增强局部暗线，并以 Hessian 方向一致性加权后做连通域形状过滤。"},
        "blackhat": {"label": "Blackhat 形态学暗线", "description": "直接检测局部暗线并做形状过滤，适合检查较明显的暗缝，也容易保留暗色纹理。"},
        "frangi": {"label": "Frangi 多尺度暗线", "description": "用多个尺度的 Hessian 特征增强暗线，再做形状过滤。对细长暗线敏感，纹理与阴影仍可能产生误检。"},
    },
}


# Presentation only; the config's Literal types remain the accepted-value contract.
_VISIBILITY = {
    "face": {
        "algorithm": {"mode": ["auto"]},
        "allow_full_image_fallback": {"mode": ["auto"]},
        "min_area_ratio": {"mode": ["auto"]},
        "max_area_ratio": {"mode": ["auto"]},
        "grabcut_iterations": {"mode": ["auto"], "algorithm": ["grabcut"]},
        "grabcut_margin": {"mode": ["auto"], "algorithm": ["grabcut"]},
    },
    "lithology": {
        "clusters": {"algorithm": ["kmeans"]},
        "spatial_weight": {"algorithm": ["kmeans"]},
        "random_seed": {"algorithm": ["kmeans"]},
        "slic_segments": {"algorithm": ["slic"]},
        "slic_compactness": {"algorithm": ["slic"]},
    },
    "fracture": {
        "blackhat_radius": {"algorithm": ["blackhat_hessian", "blackhat"]},
        "frangi_sigma_min": {"algorithm": ["frangi"]},
        "frangi_sigma_max": {"algorithm": ["frangi"]},
        "frangi_scale_count": {"algorithm": ["frangi"]},
    },
}


def ui_schema():
    parameters = {section: {key: {"label": value[0], "unit": value[1], "description": value[2]}
                            for key, value in fields.items()} for section, fields in _FIELDS.items()}
    for section, choices in _CHOICES.items():
        parameters[section]["algorithm"]["options"] = choices
    for section, fields in _VISIBILITY.items():
        for key, condition in fields.items():
            parameters[section][key]["visible_when"] = condition
    return {"schema": PipelineConfig.model_json_schema(), "defaults": PipelineConfig().model_dump(),
            "stages": STAGES, "parameters": parameters}
