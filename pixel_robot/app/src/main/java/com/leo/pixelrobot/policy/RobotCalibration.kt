package com.leo.pixelrobot.policy

import android.content.res.AssetManager
import org.json.JSONObject

class RobotCalibration private constructor(value: JSONObject) {
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
        val biasJson = imu.getJSONArray("gyro_bias_dps")
        gyroBiasDps = FloatArray(3) { biasJson.getDouble(it).toFloat() }
        gravitySign = imu.getDouble("gravity_sign").toFloat()
        require(gravitySign == -1f || gravitySign == 1f)
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

    fun bodyGyroRadPerSecond(sensorGyroDps: FloatArray): FloatArray {
        require(sensorGyroDps.size == 3 && sensorGyroDps.all(Float::isFinite))
        val unbiased = FloatArray(3) { sensorGyroDps[it] - gyroBiasDps[it] }
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
        private val EXPECTED_SERVO_IDS = intArrayOf(7, 8, 9, 1, 2, 3, 4, 5, 6, 10, 11, 12)
        private val EXPECTED_PARENTS = intArrayOf(-1, -1, 1, -1, -1, 4, -1, -1, 7, -1, -1, 10)
        private val SEMANTICS = arrayOf(
            "front_right_hip_abduction", "front_right_hip_flexion", "front_right_knee_flexion",
            "front_left_hip_abduction", "front_left_hip_flexion", "front_left_knee_flexion",
            "back_right_hip_abduction", "back_right_hip_flexion", "back_right_knee_flexion",
            "back_left_hip_abduction", "back_left_hip_flexion", "back_left_knee_flexion",
        )

        fun load(assets: AssetManager): RobotCalibration = RobotCalibration(
            JSONObject(assets.open("assembly-1-12dof.calibration.json").bufferedReader().use { it.readText() }),
        )

        fun parse(json: String): RobotCalibration = RobotCalibration(JSONObject(json))
    }
}

private fun JSONObject.finiteFloat(name: String): Float = getDouble(name).toFloat().also {
    require(it.isFinite()) { "$name must be finite" }
}

private fun Float?.orZero(): Float = this ?: 0f
