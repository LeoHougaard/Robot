package com.leo.pixelrobot.policy

import android.content.res.AssetManager
import org.json.JSONArray
import org.json.JSONObject

class RobotCalibration private constructor(value: JSONObject) {
    private val source = JSONObject(value.toString())
    val robotId: String = value.getString("robot")
    val servoIds = IntArray(JOINT_COUNT)
    val zeros = FloatArray(JOINT_COUNT)
    val degreesPerRadian = FloatArray(JOINT_COUNT)
    val minimums = FloatArray(JOINT_COUNT)
    val maximums = FloatArray(JOINT_COUNT)
    val parentIndex = IntArray(JOINT_COUNT) { -1 }
    val imuMatrix: Array<FloatArray>
    val gyroBiasDps: FloatArray
    val gravitySign: Float

    init {
        require(value.getBoolean("calibrated")) { "joint calibration is not approved" }
        val jointsJson = value.getJSONArray("joints")
        require(jointsJson.length() == JOINT_COUNT)
        val joints = (0 until jointsJson.length())
            .map { jointsJson.getJSONObject(it) }
            .sortedBy { it.getInt("policy_index") }
        joints.forEachIndexed { index, joint ->
            require(joint.getInt("policy_index") == index)
            require(joint.getString("semantic") == SEMANTICS[index]) { "joint semantic order changed" }
            servoIds[index] = joint.getInt("servo_id")
            zeros[index] = joint.finiteFloat("zero_deg")
            degreesPerRadian[index] = joint.finiteFloat("servo_degrees_per_policy_radian")
            minimums[index] = joint.finiteFloat("min_deg")
            maximums[index] = joint.finiteFloat("max_deg")
            require(minimums[index] < zeros[index] && zeros[index] < maximums[index])
            joint.optJSONObject("linkage")?.let { linkage ->
                require(linkage.getString("type") == "four_bar_follow")
                require(linkage.getDouble("parent_ratio") == 1.0)
                parentIndex[index] = linkage.getInt("parent_policy_index")
            }
        }
        require(servoIds.contentEquals(EXPECTED_SERVO_IDS)) { "physical servo mapping changed" }
        require(parentIndex.contentEquals(EXPECTED_PARENTS)) { "four-bar mapping changed" }

        val imu = value.getJSONObject("imu")
        require(imu.getBoolean("calibrated")) { "IMU calibration is not approved" }
        val matrixJson = imu.getJSONArray("body_axis_from_sensor_axis")
        imuMatrix = Array(3) { row ->
            val source = matrixJson.getJSONArray(row)
            FloatArray(3) { column -> source.getDouble(column).toFloat() }
        }
        require(imuMatrix.indices.all { row ->
            imuMatrix[row].indices.all { column ->
                kotlin.math.abs(imuMatrix[row][column] - USB_C_REAR_IMU_MATRIX[row][column]) <= IMU_MATRIX_TOLERANCE
            }
        }) { "IMU mounting must match the ESP32 board with its USB-C ports facing rear" }
        val biasJson = imu.getJSONArray("gyro_bias_dps")
        gyroBiasDps = FloatArray(3) { biasJson.getDouble(it).toFloat() }
        require(gyroBiasDps.all(Float::isFinite))
        gravitySign = imu.getDouble("gravity_sign").toFloat()
        require(gravitySign == -1f) { "ESP32 IMU must be mounted component-side up" }
    }

    fun policyPositions(anglesByServoId: Map<Int, Float>): FloatArray {
        val positions = FloatArray(JOINT_COUNT) { index ->
            val angle = requireNotNull(anglesByServoId[servoIds[index]]) { "missing servo ${servoIds[index]}" }
            require(angle.isFinite())
            (angle - zeros[index]) / degreesPerRadian[index]
        }
        parentIndex.forEachIndexed { index, parent -> if (parent >= 0) positions[index] -= positions[parent] }
        return positions
    }

    fun servoTargets(policyPositions: FloatArray): Map<Int, Float> {
        require(policyPositions.size == JOINT_COUNT && policyPositions.all(Float::isFinite))
        return buildMap(JOINT_COUNT) {
            policyPositions.forEachIndexed { index, policyPosition ->
                val transmitted = policyPosition + parentIndex[index].takeIf { it >= 0 }
                    ?.let(policyPositions::get).orZero()
                val target = zeros[index] + transmitted * degreesPerRadian[index]
                require(target.isFinite())
                require(target in minimums[index]..maximums[index]) {
                    "servo ${servoIds[index]} target $target is outside calibrated limits"
                }
                put(servoIds[index], target)
            }
        }
    }

    fun captureReference(anglesByServoId: Map<Int, Float>): CapturedCalibration {
        require(anglesByServoId.keys == EXPECTED_SERVO_IDS.toSet()) {
            "reference capture requires servo IDs 1 through 12 in policy order"
        }
        val updated = JSONObject(source.toString())
        val joints = updated.getJSONArray("joints")
        var largestShift = 0f
        for (arrayIndex in 0 until joints.length()) {
            val joint = joints.getJSONObject(arrayIndex)
            val policyIndex = joint.getInt("policy_index")
            val servoId = joint.getInt("servo_id")
            val measured = requireNotNull(anglesByServoId[servoId])
            require(measured.isFinite() && measured in 0f..360f) {
                "servo $servoId reference is outside 0 to 360 degrees"
            }
            val previousZero = zeros[policyIndex]
            val shift = measured - previousZero
            require(kotlin.math.abs(shift) <= MAX_CAPTURE_SHIFT_DEG) {
                "servo $servoId is ${"%.1f".format(kotlin.math.abs(shift))} degrees from its previous reference"
            }
            val nextMinimum = minimums[policyIndex] + shift
            val nextMaximum = maximums[policyIndex] + shift
            require(nextMinimum >= 0f && nextMinimum < measured && measured < nextMaximum && nextMaximum <= 360f) {
                "servo $servoId reference would move its safe limits outside 0 to 360 degrees"
            }
            joint.put("zero_deg", measured.toDouble())
            joint.put("min_deg", nextMinimum.toDouble())
            joint.put("max_deg", nextMaximum.toDouble())
            largestShift = maxOf(largestShift, kotlin.math.abs(shift))
        }
        val json = updated.toString(2) + "\n"
        return CapturedCalibration(RobotCalibration(JSONObject(json)), json, largestShift)
    }

    fun firmwareServoConfiguration(currentConfig: JSONObject): JSONArray {
        val currentServos = currentConfig.getJSONArray("servos")
        require(currentServos.length() == JOINT_COUNT) { "ESP32 configuration must contain 12 servos" }
        val currentById = buildMap {
            currentServos.indices().forEach { index ->
                val servo = currentServos.getJSONObject(index)
                val servoId = servo.getInt("id")
                require(put(servoId, servo) == null) { "ESP32 configuration contains duplicate servo $servoId" }
            }
        }
        require(currentById.keys == EXPECTED_SERVO_IDS.toSet()) {
            "ESP32 configuration must cover servo IDs 1 through 12"
        }

        return JSONArray().also { updated ->
            currentServos.indices().forEach { currentIndex ->
                val current = currentServos.getJSONObject(currentIndex)
                val servoId = current.getInt("id")
                val policyIndex = servoIds.indexOf(servoId)
                require(policyIndex >= 0)
                require(current.optBoolean("enabled", false)) { "ESP32 servo $servoId is disabled" }
                updated.put(
                    JSONObject()
                        .put("id", servoId)
                        .put("name", current.getString("name"))
                        .put("min", minimums[policyIndex].toDouble())
                        .put("max", maximums[policyIndex].toDouble())
                        .put("home", zeros[policyIndex].toDouble())
                        .put("invert", current.optBoolean("invert", false))
                        .put("enabled", true)
                        .put("monitor", current.optBoolean("monitor", true))
                        .put("monitorInterval", current.optInt("monitorInterval", 250)),
                )
            }
        }
    }

    fun requireFirmwareConfigurationMatches(config: JSONObject) {
        val servos = config.getJSONArray("servos")
        require(servos.length() == JOINT_COUNT) { "ESP32 configuration must contain 12 servos" }
        val byId = buildMap {
            servos.indices().forEach { index ->
                val servo = servos.getJSONObject(index)
                val servoId = servo.getInt("id")
                require(put(servoId, servo) == null) { "ESP32 configuration contains duplicate servo $servoId" }
            }
        }
        require(byId.keys == EXPECTED_SERVO_IDS.toSet()) {
            "ESP32 configuration must cover servo IDs 1 through 12"
        }
        servoIds.indices.forEach { policyIndex ->
            val servoId = servoIds[policyIndex]
            val servo = requireNotNull(byId[servoId])
            require(servo.optBoolean("enabled", false)) { "ESP32 servo $servoId is disabled" }
            require(kotlin.math.abs(servo.getDouble("min").toFloat() - minimums[policyIndex]) <= FIRMWARE_ANGLE_TOLERANCE_DEG) {
                "ESP32 servo $servoId minimum does not match the captured calibration"
            }
            require(kotlin.math.abs(servo.getDouble("home").toFloat() - zeros[policyIndex]) <= FIRMWARE_ANGLE_TOLERANCE_DEG) {
                "ESP32 servo $servoId home does not match the captured zero"
            }
            require(kotlin.math.abs(servo.getDouble("max").toFloat() - maximums[policyIndex]) <= FIRMWARE_ANGLE_TOLERANCE_DEG) {
                "ESP32 servo $servoId maximum does not match the captured calibration"
            }
        }
    }

    fun maximumReferenceErrorDegrees(anglesByServoId: Map<Int, Float>): Float {
        require(anglesByServoId.keys.containsAll(servoIds.toSet()))
        return servoIds.indices.maxOf { index ->
            kotlin.math.abs(requireNotNull(anglesByServoId[servoIds[index]]) - zeros[index])
        }
    }

    fun bodyGyroRadPerSecond(
        sensorGyroDps: FloatArray,
        sensorBiasDps: FloatArray = gyroBiasDps,
    ): FloatArray {
        require(sensorGyroDps.size == 3 && sensorGyroDps.all(Float::isFinite))
        require(sensorBiasDps.size == 3 && sensorBiasDps.all(Float::isFinite))
        val unbiased = FloatArray(3) { sensorGyroDps[it] - sensorBiasDps[it] }
        return matVec(imuMatrix, unbiased).also { values ->
            for (index in values.indices) values[index] *= DEG_TO_RAD
        }
    }

    fun bodyAccelerationMg(sensorAccelerationMg: FloatArray): FloatArray {
        require(sensorAccelerationMg.size == 3 && sensorAccelerationMg.all(Float::isFinite))
        return matVec(imuMatrix, sensorAccelerationMg)
    }

    companion object {
        const val JOINT_COUNT = 12
        private const val DEG_TO_RAD = (Math.PI / 180.0).toFloat()
        private const val IMU_MATRIX_TOLERANCE = 1.0e-5f
        private const val FIRMWARE_ANGLE_TOLERANCE_DEG = 0.001f
        private const val MAX_CAPTURE_SHIFT_DEG = 45f
        private val USB_C_REAR_IMU_MATRIX = arrayOf(
            floatArrayOf(0f, 1f, 0f),
            floatArrayOf(-1f, 0f, 0f),
            floatArrayOf(0f, 0f, 1f),
        )
        private val EXPECTED_SERVO_IDS = intArrayOf(7, 8, 9, 1, 2, 3, 4, 5, 6, 10, 11, 12)
        private val EXPECTED_PARENTS = intArrayOf(-1, -1, 1, -1, -1, 4, -1, -1, 7, -1, -1, 10)
        private val SEMANTICS = arrayOf(
            "front_right_hip_abduction", "front_right_hip_flexion", "front_right_knee_flexion",
            "front_left_hip_abduction", "front_left_hip_flexion", "front_left_knee_flexion",
            "back_right_hip_abduction", "back_right_hip_flexion", "back_right_knee_flexion",
            "back_left_hip_abduction", "back_left_hip_flexion", "back_left_knee_flexion",
        )

        fun load(
            assets: AssetManager,
            profileId: String,
            overrideJson: String? = null,
        ): RobotCalibration {
            require(profileId.matches(Regex("[a-z0-9][a-z0-9-]{0,63}")))
            return RobotCalibration(
                JSONObject(
                    overrideJson
                        ?: assets.open("$profileId.calibration.json").bufferedReader().use { it.readText() },
                ),
            ).also { require(it.robotId == profileId) { "physical calibration does not match policy profile" } }
        }

        fun parse(json: String): RobotCalibration = RobotCalibration(JSONObject(json))
    }
}

private fun JSONArray.indices(): IntRange = 0 until length()

data class CapturedCalibration(
    val calibration: RobotCalibration,
    val json: String,
    val largestShiftDegrees: Float,
)

private fun JSONObject.finiteFloat(name: String): Float = getDouble(name).toFloat().also {
    require(it.isFinite()) { "$name must be finite" }
}

private fun Float?.orZero(): Float = this ?: 0f
